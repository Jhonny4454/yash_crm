"""
blueprints/api/permissions.py
=============================

What each staff user is allowed to do.

Why this exists
---------------
Until now there were exactly two levels of access: ``admin`` and
"everybody else". `role` is an Enum with four values, but only ``admin`` was
ever checked - `admin_required` tests ``role == 'admin'`` and `staff_required`
tests nothing beyond "is a live staff account". So a support user, a field
engineer and an accounts clerk all saw and could do exactly the same things,
and the only way to let somebody renew a plan was to make them an
administrator, which also let them edit staff, rewrite settings and restore
backups.

This module adds a second axis: a per-user list of capabilities that an
administrator ticks when creating or editing the user. Role stays as it is -
it still decides who is an administrator - and capabilities decide what
everybody else can reach.

Two decisions worth stating plainly
-----------------------------------
1. **An empty permission list means "unrestricted", not "nothing".**
   Every user who exists today has an empty list, and reading that as "denied"
   would lock the whole company out of their own CRM on the deploy that
   introduced this file. A user is restricted from the moment an administrator
   ticks the first box for them, and not before. `is_restricted()` is the one
   place that rule lives.

2. **Enforcement is a path table, not 209 decorators.**
   The rules below are matched against the request path inside
   `staff_required`, which every staff endpoint already goes through. One table
   that can be read top to bottom beats a decorator on each endpoint, where the
   one somebody forgets is invisible and the mistake is silent.
"""
import re

from flask import request

#: Everything an administrator can grant, in the order the screen shows them.
#:
#: `group` only drives the headings on the Staff screen. `key` is what is
#: stored, so renaming one is a data migration - add a new key instead.
CAPABILITIES = [
    # --- customers ---
    {'key': 'customers.view', 'group': 'Customers', 'label': 'View customers'},
    {'key': 'customers.edit', 'group': 'Customers', 'label': 'Add and edit customers'},
    # Separate from `edit` on purpose. Removing a customer is not a heavier
    # kind of editing - it is the one customer action that cannot be undone
    # from the screen that did it, so it is its own tick box and nobody gets
    # it by implication.
    {'key': 'customers.delete', 'group': 'Customers', 'label': 'Delete customers'},
    {'key': 'plans.renew', 'group': 'Customers', 'label': 'Renew and extend plans'},

    # --- money ---
    {'key': 'invoices.view', 'group': 'Billing', 'label': 'View invoices'},
    {'key': 'invoices.create', 'group': 'Billing', 'label': 'Raise invoices and run billing'},
    {'key': 'payments.record', 'group': 'Billing', 'label': 'Record payments'},
    {'key': 'payments.authorise', 'group': 'Billing', 'label': 'Authorise payments'},
    {'key': 'expenses.manage', 'group': 'Billing', 'label': 'Expenses'},

    # --- talking to customers ---
    {'key': 'messages.send', 'group': 'Messaging', 'label': 'Send WhatsApp / SMS'},

    # --- the rest of the back office ---
    {'key': 'reports.view', 'group': 'Back office', 'label': 'Reports'},
    {'key': 'masters.manage', 'group': 'Back office', 'label': 'Masters (zones, areas, plans, templates)'},
    {'key': 'inventory.manage', 'group': 'Back office', 'label': 'Inventory'},
    {'key': 'hr.manage', 'group': 'Back office', 'label': 'HR, attendance and payroll'},

    # --- keys to the building ---
    {'key': 'staff.manage', 'group': 'Administration', 'label': 'Create and edit staff'},
    {'key': 'settings.manage', 'group': 'Administration', 'label': 'Settings, backups, integrations'},
]

CAPABILITY_KEYS = [c['key'] for c in CAPABILITIES]
_VALID = set(CAPABILITY_KEYS)

#: Read access is implied by write access. Somebody who can record a payment
#: obviously has to be able to see the invoice they are recording it against,
#: and making an administrator tick both boxes for that is a trap: they tick
#: "record payments", the user gets a 403 on the invoice list, and it reads as
#: a bug rather than as a missing tick.
IMPLIES = {
    'customers.edit': ('customers.view',),
    'plans.renew': ('customers.view',),
    'invoices.create': ('invoices.view', 'customers.view'),
    'payments.record': ('invoices.view', 'customers.view'),
    'payments.authorise': ('invoices.view', 'customers.view'),
    'messages.send': ('customers.view',),
}


def expand(keys):
    """A permission set plus everything those permissions imply."""
    out = set()
    for key in keys or ():
        if key not in _VALID:
            continue
        out.add(key)
        out.update(IMPLIES.get(key, ()))
    return out


def parse(raw):
    """Stored text -> a clean list of known capability keys.

    Stored as comma-separated text rather than JSON so the column is readable
    in a database client and greppable in a backup, and so it works unchanged
    on MySQL 5.7, which has no usable JSON column type.
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = str(raw).replace('\n', ',').split(',')
    seen, out = set(), []
    for item in items:
        key = str(item).strip()
        if key in _VALID and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def serialise(keys):
    """A capability list -> the text stored on the user row."""
    return ','.join(parse(keys))


def is_admin(user):
    return getattr(user, 'role', None) == 'admin'


def is_restricted(user):
    """Whether this user's access is limited to their ticked capabilities.

    Administrators never are. Neither is anybody whose list is empty - see the
    note at the top of this file: an empty list is "nobody has set this up for
    them yet", which must keep meaning what it meant before this file existed.
    """
    if user is None or is_admin(user):
        return False
    return bool(parse(getattr(user, 'permissions', None)))


def effective(user):
    """The capability set to answer `can this user...` with.

    An administrator gets everything, always - including capabilities added by
    a future release, which is the point of computing it rather than storing
    it. An unrestricted user likewise, so the API answers the same way it did
    before anybody was given a permission list.
    """
    if user is None:
        return set()
    if not is_restricted(user):
        return set(CAPABILITY_KEYS)
    return expand(parse(user.permissions))


def can(user, capability):
    return capability in effective(user)


# --------------------------------------------------------------------------- #
#  Which capability each endpoint needs
#
#  Matched in order, first hit wins, against the path AFTER the /api/v1 prefix.
#  A path that matches nothing here needs no capability - that is deliberate:
#  a new endpoint should be reachable until somebody decides otherwise, rather
#  than silently 403ing for every non-administrator the day it ships.
#
#  `None` for a method set means "every method".
# --------------------------------------------------------------------------- #
WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')

RULES = [
    # Things every signed-in staff member needs regardless of their list -
    # their own profile, the branding, the lookup lists the forms are built
    # from. Gating these would leave a restricted user staring at empty
    # dropdowns and unable to change their own password.
    (r'^/auth/', None, None),
    (r'^/health$', None, None),
    (r'^/profile', None, None),
    (r'^/notifications', None, None),
    (r'^/dashboard', ('GET',), None),

    # Administration
    (r'^/(staff|users)\b', None, 'staff.manage'),
    (r'^/settings', None, 'settings.manage'),
    (r'^/isp/', None, 'settings.manage'),

    # Messaging
    (r'^/messages/', None, 'messages.send'),
    (r'^/reports/plan-expiry/notify', None, 'messages.send'),

    # Renewing and extending a plan. Listed before the generic customer and
    # report rules so "can only renew" is exactly what it says.
    (r'^/reports/plan-expiry/renew', None, 'plans.renew'),
    (r'^/customer-plans/\d+/dates', None, 'plans.renew'),
    (r'^/customer-plans', WRITE_METHODS, 'plans.renew'),
    (r'^/renewals', WRITE_METHODS, 'plans.renew'),
    # The expiry board itself. EITHER capability opens it: it is the screen
    # somebody renews from, so gating it behind reports.view would mean
    # "can only renew" produced a user who could renew and had no way to
    # reach the list of who needs renewing.
    (r'^/reports/plan-expiry', ('GET',), ('plans.renew', 'reports.view')),
    (r'^/renewals', ('GET',), ('plans.renew', 'reports.view')),

    # Money
    (r'^/payments/authoris', None, 'payments.authorise'),
    (r'^/payments/authoriz', None, 'payments.authorise'),
    (r'^/payments', WRITE_METHODS, 'payments.record'),
    (r'^/customers/\d+/payments', WRITE_METHODS, 'payments.record'),
    (r'^/billing-run', None, 'invoices.create'),
    (r'^/invoices', WRITE_METHODS, 'invoices.create'),
    (r'^/invoices', ('GET',), 'invoices.view'),
    (r'^/expenses', None, 'expenses.manage'),

    # Back office
    (r'^/inventory/', None, 'inventory.manage'),
    (r'^/hr/', None, 'hr.manage'),
    (r'^/reports/', ('GET',), 'reports.view'),
    (r'^/masters/', WRITE_METHODS, 'masters.manage'),
    (r'^/plans', WRITE_METHODS, 'masters.manage'),
    (r'^/service-providers', WRITE_METHODS, 'masters.manage'),
    (r'^/companies', WRITE_METHODS, 'masters.manage'),

    # Customers last, because several rules above are sub-paths of it.
    # Deleting comes first of the three: it is a DELETE on the bare customer
    # path, which the generic write rule below would otherwise wave through on
    # `customers.edit`.
    (r'^/customers/\d+$', ('DELETE',), 'customers.delete'),
    (r'^/customers', WRITE_METHODS, 'customers.edit'),
    (r'^/customers', ('GET',), 'customers.view'),
]

_COMPILED = [(re.compile(pattern), methods, capability)
             for pattern, methods, capability in RULES]

API_PREFIX = '/api/v1'


def required_for(path, method):
    """The capabilities this request needs, or None if it needs none.

    Returns a tuple, and holding ANY ONE of them is enough. Several screens
    are legitimately reachable from two directions - the expiry board is both
    a report and the thing you renew from - and forcing an administrator to
    tick a second box to make the first one usable is a trap, not a policy.
    """
    trimmed = path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path
    for pattern, methods, capability in _COMPILED:
        if not pattern.match(trimmed):
            continue
        if methods and method.upper() not in methods:
            continue
        if capability is None:
            return None
        return (capability,) if isinstance(capability, str) else tuple(capability)
    return None


def check(user):
    """None when the request is allowed, or the capability that is missing."""
    if user is None or not is_restricted(user):
        return None
    needed = required_for(request.path, request.method)
    if not needed:
        return None
    held = effective(user)
    if any(capability in held for capability in needed):
        return None
    # Name the first one, which is the one an administrator would tick.
    return needed[0]


def describe(user):
    """What the browser needs to hide the controls this user cannot use."""
    return {
        'restricted': is_restricted(user),
        'granted': sorted(effective(user)),
        'catalogue': CAPABILITIES,
    }
