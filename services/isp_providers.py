"""
services/isp_providers.py
=========================

Pluggable integration layer for radius / provisioning back-ends.

Design goals
------------
1.  app.py never talks to a vendor API directly. It calls
    ``provision(customer, 'enable')`` and this module works out which provider
    that customer's plan belongs to, which credential row to use, and which
    adapter class implements it.
2.  Adding a new provider is one subclass + one ``@register`` line. No changes
    anywhere else in the codebase.
3.  Every outbound call is logged to ``isp_sync_logs`` with timing and status,
    so when a customer says "my connection was not enabled" you can prove what
    happened.
4.  A failure NEVER takes down the CRM operation. Provisioning is best-effort
    and reports back via a ``ProvisionResult``; the caller decides whether to
    flash a warning.

Adding a provider
-----------------
    @register('myisp')
    class MyISPAdapter(BaseAdapter):
        def enable(self, customer, **kw): ...
        def disable(self, customer, **kw): ...

Then create an ISPCredential row with driver='myisp'.
"""
from __future__ import annotations

import time
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

import requests

log = logging.getLogger(__name__)

_REGISTRY: Dict[str, type] = {}


def register(name: str):
    """Class decorator that adds an adapter to the driver registry."""
    def _wrap(cls):
        cls.driver_name = name
        _REGISTRY[name] = cls
        return cls
    return _wrap


def available_drivers():
    """[('log2space', 'Log2Space'), ('synnefo', 'Synnefo'), ...] for form choices."""
    return sorted((name, cls.display_name or name.title())
                  for name, cls in _REGISTRY.items())


# --------------------------------------------------------------------------- #
#  Result object
# --------------------------------------------------------------------------- #
@dataclass
class ProvisionResult:
    ok: bool
    action: str
    message: str = ''
    http_status: Optional[int] = None
    raw: Any = None
    duration_ms: int = 0
    skipped: bool = False          # True when no provider is configured

    def __bool__(self):
        return self.ok

    @classmethod
    def skip(cls, action, message):
        return cls(ok=True, action=action, message=message, skipped=True)


class ProviderError(Exception):
    """Raised inside an adapter when the remote side rejects a request."""


# --------------------------------------------------------------------------- #
#  Base adapter
# --------------------------------------------------------------------------- #
class BaseAdapter:
    """
    Subclass this for each provider. `credential` is an ISPCredential row.

    Only override the operations your provider actually supports; anything left
    alone raises NotImplementedError, which is caught and reported cleanly.
    """
    driver_name = 'base'
    display_name = 'Base'

    def __init__(self, credential):
        self.cred = credential
        self.opts = credential.options or {}
        self._session = requests.Session()

    # ---- plumbing --------------------------------------------------------- #
    @property
    def base_url(self):
        return (self.cred.base_url or '').rstrip('/')

    def _headers(self):
        h = {'Accept': 'application/json',
             'Content-Type': 'application/json',
             'User-Agent': 'YASH-CRM/1.0'}
        if self.cred.api_key:
            h['Authorization'] = f"Bearer {self.cred.api_key}"
        return h

    def _request(self, method, path, **kwargs):
        url = path if path.startswith('http') else f"{self.base_url}/{path.lstrip('/')}"
        kwargs.setdefault('timeout', self.cred.timeout_seconds or 20)
        kwargs.setdefault('verify', bool(self.cred.verify_ssl))
        headers = self._headers()
        headers.update(kwargs.pop('headers', {}) or {})
        resp = self._session.request(method, url, headers=headers, **kwargs)
        return resp

    @staticmethod
    def _json(resp):
        try:
            return resp.json()
        except ValueError:
            return {'_raw': resp.text[:2000]}

    # ---- operations (override in subclasses) ------------------------------ #
    def test_connection(self) -> ProvisionResult:
        raise NotImplementedError

    def enable(self, customer, **kw) -> ProvisionResult:
        raise NotImplementedError

    def disable(self, customer, **kw) -> ProvisionResult:
        raise NotImplementedError

    def renew(self, customer, plan, start_date, end_date, **kw) -> ProvisionResult:
        raise NotImplementedError

    def change_plan(self, customer, plan, **kw) -> ProvisionResult:
        raise NotImplementedError

    def reset_password(self, customer, new_password, **kw) -> ProvisionResult:
        raise NotImplementedError

    def reset_mac(self, customer, mac_address=None, **kw) -> ProvisionResult:
        raise NotImplementedError

    def create_user(self, customer, plan, **kw) -> ProvisionResult:
        raise NotImplementedError

    def usage(self, customer, **kw) -> ProvisionResult:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
#  Log2Space
# --------------------------------------------------------------------------- #
@register('log2space')
class Log2SpaceAdapter(BaseAdapter):
    """
    Adapter for Log2Space (isp.<yourdomain>/admin).

    !! ENDPOINT PATHS BELOW ARE PLACEHOLDERS !!
    Log2Space does not publish a public API spec, and the paths differ between
    deployments. Get the API document from your Log2Space account manager, then
    correct the five constants below — nothing else needs to change.

    Set credential.options to something like:
        {"site": "AIROLI", "nas": "NAS1"}
    """
    display_name = 'Log2Space'

    EP_PING = '/api/v1/ping'
    EP_USER = '/api/v1/users/{username}'
    EP_STATUS = '/api/v1/users/{username}/status'
    EP_RENEW = '/api/v1/users/{username}/renew'
    EP_PASSWORD = '/api/v1/users/{username}/password'
    EP_MAC = '/api/v1/users/{username}/mac'
    EP_USAGE = '/api/v1/users/{username}/usage'

    def _headers(self):
        h = super()._headers()
        # Log2Space installs commonly use a username + API-key header pair
        # rather than a bearer token. Adjust to match your deployment.
        if self.cred.username:
            h['X-Api-User'] = self.cred.username
        secret = self.cred.get_secret()
        if secret:
            h['X-Api-Key'] = secret
            h.pop('Authorization', None)
        return h

    def _remote_username(self, customer):
        """Log2Space login for this customer — usually the PPPoE username."""
        return (customer.username or customer.reference_id or '').strip()

    def _payload_base(self, customer):
        return {
            'username': self._remote_username(customer),
            'site': self.opts.get('site'),
            'nas': self.opts.get('nas'),
        }

    def test_connection(self):
        r = self._request('GET', self.EP_PING)
        return ProvisionResult(ok=r.ok, action='test',
                               message=f"HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def enable(self, customer, **kw):
        u = self._remote_username(customer)
        r = self._request('POST', self.EP_STATUS.format(username=u),
                          json={**self._payload_base(customer),
                                'status': 'active'})
        return ProvisionResult(ok=r.ok, action='enable',
                               message='Connection enabled' if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def disable(self, customer, **kw):
        u = self._remote_username(customer)
        r = self._request('POST', self.EP_STATUS.format(username=u),
                          json={**self._payload_base(customer),
                                'status': 'suspended',
                                'reason': kw.get('reason', 'Non-payment')})
        return ProvisionResult(ok=r.ok, action='disable',
                               message='Connection suspended' if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def renew(self, customer, plan, start_date, end_date, **kw):
        u = self._remote_username(customer)
        r = self._request('POST', self.EP_RENEW.format(username=u),
                          json={**self._payload_base(customer),
                                'plan_code': getattr(plan, 'plan_code', None) or plan.name,
                                'valid_from': start_date.isoformat(),
                                'valid_to': end_date.isoformat()})
        return ProvisionResult(ok=r.ok, action='renew',
                               message=f"Renewed to {end_date:%d-%b-%Y}" if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def change_plan(self, customer, plan, **kw):
        u = self._remote_username(customer)
        r = self._request('PATCH', self.EP_USER.format(username=u),
                          json={**self._payload_base(customer),
                                'plan_code': getattr(plan, 'plan_code', None) or plan.name})
        return ProvisionResult(ok=r.ok, action='change_plan',
                               message='Plan changed' if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def reset_password(self, customer, new_password, **kw):
        u = self._remote_username(customer)
        r = self._request('POST', self.EP_PASSWORD.format(username=u),
                          json={**self._payload_base(customer),
                                'password': new_password})
        # Never log the password itself.
        return ProvisionResult(ok=r.ok, action='reset_password',
                               message='Password reset' if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw={'ok': r.ok})

    def reset_mac(self, customer, mac_address=None, **kw):
        u = self._remote_username(customer)
        body = {**self._payload_base(customer)}
        if mac_address:
            body['mac'] = mac_address
        else:
            body['clear'] = True
        r = self._request('POST', self.EP_MAC.format(username=u), json=body)
        return ProvisionResult(ok=r.ok, action='reset_mac',
                               message='MAC binding cleared' if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def create_user(self, customer, plan, **kw):
        r = self._request('POST', '/api/v1/users', json={
            **self._payload_base(customer),
            'password': kw.get('password'),
            'full_name': customer.full_name,
            'mobile': customer.mobile,
            'email': customer.email,
            'plan_code': getattr(plan, 'plan_code', None) or (plan.name if plan else None),
        })
        return ProvisionResult(ok=r.ok, action='create_user',
                               message='Subscriber created' if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def usage(self, customer, **kw):
        u = self._remote_username(customer)
        r = self._request('GET', self.EP_USAGE.format(username=u),
                          params={'from': kw.get('date_from'),
                                  'to': kw.get('date_to')})
        return ProvisionResult(ok=r.ok, action='usage',
                               message='', http_status=r.status_code,
                               raw=self._json(r))


# --------------------------------------------------------------------------- #
#  Synnefo
# --------------------------------------------------------------------------- #
@register('synnefo')
class SynnefoAdapter(BaseAdapter):
    """
    Adapter for Synnefo. Same caveat as above: correct the endpoint constants
    against the API doc your provider gives you.
    """
    display_name = 'Synnefo'

    EP_PING = '/api/ping'
    EP_SUBSCRIBER = '/api/subscriber/{username}'
    EP_ACTION = '/api/subscriber/{username}/action'

    def _remote_username(self, customer):
        return (customer.username or customer.reference_id or '').strip()

    def test_connection(self):
        r = self._request('GET', self.EP_PING)
        return ProvisionResult(ok=r.ok, action='test',
                               message=f"HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def _action(self, customer, action, extra=None):
        u = self._remote_username(customer)
        body = {'action': action}
        body.update(extra or {})
        r = self._request('POST', self.EP_ACTION.format(username=u), json=body)
        return ProvisionResult(ok=r.ok, action=action,
                               message=f"{action} ok" if r.ok
                                       else f"Provider refused: HTTP {r.status_code}",
                               http_status=r.status_code, raw=self._json(r))

    def enable(self, customer, **kw):
        return self._action(customer, 'activate')

    def disable(self, customer, **kw):
        return self._action(customer, 'suspend',
                            {'reason': kw.get('reason', 'Non-payment')})

    def renew(self, customer, plan, start_date, end_date, **kw):
        return self._action(customer, 'renew', {
            'plan': getattr(plan, 'plan_code', None) or plan.name,
            'from': start_date.isoformat(),
            'to': end_date.isoformat()})

    def change_plan(self, customer, plan, **kw):
        return self._action(customer, 'change_plan', {
            'plan': getattr(plan, 'plan_code', None) or plan.name})

    def reset_password(self, customer, new_password, **kw):
        return self._action(customer, 'set_password',
                            {'password': new_password})

    def reset_mac(self, customer, mac_address=None, **kw):
        return self._action(customer, 'reset_mac', {'mac': mac_address})


# --------------------------------------------------------------------------- #
#  Null adapter — used when nothing is configured
# --------------------------------------------------------------------------- #
@register('none')
class NullAdapter(BaseAdapter):
    """Logs the intent and succeeds. Keeps the CRM usable before any ISP is set up."""
    display_name = 'None (log only)'

    def test_connection(self):
        return ProvisionResult.skip('test', 'No provider configured')

    def __getattr__(self, item):
        if item in ('enable', 'disable', 'renew', 'change_plan',
                    'reset_password', 'reset_mac', 'create_user', 'usage'):
            def _noop(*a, **kw):
                return ProvisionResult.skip(item, 'No ISP provider configured')
            return _noop
        raise AttributeError(item)


# --------------------------------------------------------------------------- #
#  Resolution + dispatch
# --------------------------------------------------------------------------- #
def credential_for_customer(customer):
    """
    Work out which ISPCredential governs this customer.

    Order of preference:
      1. Credential attached to the service provider of the customer's active plan
      2. Credential attached to the service provider of the most recent plan
      3. Any single active credential in the system (small single-ISP shops)
      4. None
    """
    from models import CustomerPlan
    from models_ext import ISPCredential

    plan_rows = (CustomerPlan.query
                 .filter_by(customer_id=customer.id)
                 .order_by(CustomerPlan.status == 'active',
                           CustomerPlan.end_date.desc())
                 .all())
    for cp in plan_rows:
        sp_id = getattr(cp.plan, 'service_provider_id', None) if cp.plan else None
        if not sp_id:
            continue
        cred = (ISPCredential.query
                .filter_by(service_provider_id=sp_id, is_active=True)
                .first())
        if cred:
            return cred

    actives = ISPCredential.query.filter_by(is_active=True).all()
    return actives[0] if len(actives) == 1 else None


def adapter_for(credential):
    if credential is None:
        return NullAdapter(_DummyCred())
    cls = _REGISTRY.get(credential.driver or 'none', NullAdapter)
    return cls(credential)


class _DummyCred:
    base_url = ''
    username = None
    api_key = None
    verify_ssl = True
    timeout_seconds = 10
    options = {}

    def get_secret(self):
        return None


def provision(customer, action, **kwargs) -> ProvisionResult:
    """
    Single entry point used by app.py.

        result = provision(customer, 'enable')
        result = provision(customer, 'renew', plan=plan,
                           start_date=s, end_date=e)

    Always returns a ProvisionResult — never raises. Writes an ISPSyncLog row.
    """
    from models import db
    from models_ext import ISPSyncLog

    cred = credential_for_customer(customer)
    adapter = adapter_for(cred)
    started = time.perf_counter()

    try:
        fn = getattr(adapter, action, None)
        if fn is None:
            result = ProvisionResult(ok=False, action=action,
                                     message=f"Unknown action '{action}'")
        else:
            result = fn(customer, **kwargs)
    except NotImplementedError:
        result = ProvisionResult(
            ok=False, action=action,
            message=f"'{action}' is not supported by "
                    f"{getattr(adapter, 'display_name', 'this provider')}")
    except requests.Timeout:
        result = ProvisionResult(ok=False, action=action,
                                 message='Provider timed out — the CRM change '
                                         'was saved but the network was not '
                                         'updated. Retry from the customer page.')
    except requests.ConnectionError as e:
        result = ProvisionResult(ok=False, action=action,
                                 message=f'Cannot reach provider: {e.__class__.__name__}')
    except ProviderError as e:
        result = ProvisionResult(ok=False, action=action, message=str(e))
    except Exception:                                   # noqa: BLE001
        log.exception("Unhandled provisioning error (%s)", action)
        result = ProvisionResult(ok=False, action=action,
                                 message='Unexpected provisioning error — see server log.')

    result.duration_ms = int((time.perf_counter() - started) * 1000)

    # ---- audit ---------------------------------------------------------- #
    try:
        db.session.add(ISPSyncLog(
            credential_id=cred.id if cred else None,
            customer_id=customer.id,
            action=action,
            request_summary=json.dumps(
                {k: str(v)[:120] for k, v in kwargs.items()
                 if k not in ('password', 'new_password')})[:2000],
            response_summary=(result.message or '')[:2000],
            http_status=result.http_status,
            success=result.ok,
            duration_ms=result.duration_ms,
        ))
        if cred is not None and not result.skipped:
            from datetime import datetime
            if result.ok:
                cred.last_ok_at = datetime.utcnow()
                cred.last_error = None
            else:
                cred.last_error = (result.message or '')[:500]
        db.session.commit()
    except Exception:                                   # noqa: BLE001
        db.session.rollback()
        log.exception("Could not write ISPSyncLog")

    return result


def test_credential(credential) -> ProvisionResult:
    """Used by the 'Test connection' button on the ISP settings screen."""
    from models import db
    from datetime import datetime

    adapter = adapter_for(credential)
    started = time.perf_counter()
    try:
        result = adapter.test_connection()
    except Exception as e:                              # noqa: BLE001
        result = ProvisionResult(ok=False, action='test',
                                 message=f"{e.__class__.__name__}: {e}")
    result.duration_ms = int((time.perf_counter() - started) * 1000)

    credential.last_ok_at = datetime.utcnow() if result.ok else credential.last_ok_at
    credential.last_error = None if result.ok else (result.message or '')[:500]
    db.session.commit()
    return result
