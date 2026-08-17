"""
blueprints/api/masters.py
=========================

Generic master-data CRUD for the React admin panel.

The Jinja2 app had a list.html + form.html pair per master table. They differ
only in columns, so one factory here serves all of them and every screen in
the SPA gets the same behaviour: search, paginate, create, update, delete.

Every table registered in REGISTRY below gets, automatically::

    GET    /api/v1/<slug>            ?q=&page=&per_page=
    GET    /api/v1/<slug>/<id>
    POST   /api/v1/<slug>
    PUT    /api/v1/<slug>/<id>
    DELETE /api/v1/<slug>/<id>       (admin only, 409 if referenced)

Adding a new master table is one line at the bottom of this file.
"""
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from models import (Address, Area, Attendance, Building, DiscountReason,
                    Expense, ExpenseAccount, ExpenseCategory, ExpensePayee,
                    Leave, Locality, MessageTemplate, Payroll, Product,
                    StaffType, Stock, TaxMaster, Vendor, VendorBill, Zone,
                    AddonCategory, db)

from .utils import (admin_required, body, enum_values, fail, ok, paginate,
                    staff_required)

bp = Blueprint('api_masters', __name__)

#: Columns never accepted from the client.
_PROTECTED = {'id', 'created_at', 'updated_at', 'password_hash'}


def _columns(model):
    """Writable column names for a model."""
    return [c.key for c in model.__mapper__.columns if c.key not in _PROTECTED]


def _coerce(model, field, value):
    """Cast an incoming JSON value to the column's python type."""
    col = model.__mapper__.columns.get(field)
    if col is None:
        return value
    if value is None or value == '':
        return None

    try:
        py = col.type.python_type
    except (NotImplementedError, AttributeError):
        return value

    try:
        if py is bool:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'y')
        if py is int:
            return int(value)
        if py is float:
            return float(value)
        if py is Decimal:
            return Decimal(str(value))
        if py is date:
            if isinstance(value, date) and not isinstance(value, datetime):
                return value
            return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        if py is datetime:
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value))
        return py(value) if py is not str else str(value)
    except (TypeError, ValueError):
        return value


def _serialize(row):
    """Model -> plain dict, dates as ISO strings, Decimals as floats."""
    out = {}
    for col in row.__mapper__.columns:
        value = getattr(row, col.key, None)
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime):
            value = value.isoformat(timespec='seconds')
        elif isinstance(value, date):
            value = value.isoformat()
        out[col.key] = value
    return out


def _apply(row, model, data):
    """Copy `data` onto `row`. Returns (row, errors).

    Enum columns are checked here rather than left to the database, because
    "left to the database" turned out to mean "not checked at all". SQLite
    accepts anything into an Enum column, so a value the ORM cannot read back -
    an attendance status of 'leave' when the column allowed only present /
    absent / half-day - was written and committed happily, and then every
    subsequent SELECT of that table raised LookupError. One bad dropdown
    choice took the whole Attendance screen down until the row was deleted by
    hand. MySQL is kinder but not safe either: outside strict mode it silently
    stores an empty string instead.

    A 400 naming the field and its legal values is the only version of this
    that an operator can act on.
    """
    errors = []
    for field in _columns(model):
        if field not in data:
            continue
        value = _coerce(model, field, data[field])
        allowed = enum_values(model, field)
        if allowed and value is not None and value not in allowed:
            errors.append({
                'field': field,
                'message': (f"{field.replace('_', ' ').capitalize()} must be one "
                            f"of: {', '.join(allowed)} (got \"{value}\")."),
                'allowed': allowed,
            })
            continue
        setattr(row, field, value)
    return row, errors


def register(slug, model, search_fields=(), required=(), order_by=None,
             admin_write=False, extra=None):
    """Attach the five standard routes for one model."""
    endpoint = slug.replace('/', '_').replace('-', '_')
    guard = admin_required if admin_write else staff_required

    @bp.get('/' + slug, endpoint=endpoint + '_list')
    @staff_required
    def _list(_model=model, _search=search_fields, _order=order_by):
        query = _model.query
        q = (request.args.get('q') or '').strip()
        if q and _search:
            from .utils import escape_like
            like = f'%{escape_like(q)}%'
            query = query.filter(or_(*[getattr(_model, f).ilike(like, escape='\\')
                                       for f in _search
                                       if hasattr(_model, f)]))
        for key, value in request.args.items():
            if key in ('q', 'page', 'per_page', 'token'):
                continue
            if hasattr(_model, key) and value != '':
                query = query.filter(
                    getattr(_model, key) == _coerce(_model, key, value))

        col = _order or ('name' if hasattr(_model, 'name') else 'id')
        if hasattr(_model, col):
            query = query.order_by(getattr(_model, col))
        rows, meta = paginate(query)

        # Per-status counts over the WHOLE book, not the page on screen.
        # A list screen with status chips (e.g. Leave: pending/approved/
        # rejected) used to count the rows it happened to be showing, so
        # "Pending (2)" was only ever true for the current page - and when the
        # list was already filtered to one status, every other chip read (0).
        summary = None
        if enum_values(_model, 'status'):
            grouped = dict(db.session.query(_model.status,
                                            func.count(_model.id))
                           .group_by(_model.status).all())
            summary = {value: int(grouped.get(value, 0))
                       for value in enum_values(_model, 'status')}

        return ok([_serialize(r) for r in rows], meta=meta, summary=summary)

    @bp.get('/' + slug + '/<int:rid>', endpoint=endpoint + '_get')
    @staff_required
    def _get(rid, _model=model):
        row = db.session.get(_model, rid)
        if not row:
            return fail('not_found', 404)
        return ok(_serialize(row))

    @bp.post('/' + slug, endpoint=endpoint + '_create')
    @guard
    def _create(_model=model, _required=required):
        data = body()
        missing = [f for f in _required if not data.get(f)]
        if missing:
            return fail('missing_fields', 400, fields=missing)
        row, invalid = _apply(_model(), _model, data)
        if invalid:
            return fail('invalid_values', 400, fields=invalid,
                        detail=' '.join(e['message'] for e in invalid))
        db.session.add(row)
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            return fail('duplicate_or_invalid', 409, detail=str(exc.orig)[:200])
        return ok(_serialize(row)), 201

    @bp.put('/' + slug + '/<int:rid>', endpoint=endpoint + '_update')
    @guard
    def _update(rid, _model=model):
        row = db.session.get(_model, rid)
        if not row:
            return fail('not_found', 404)
        _, invalid = _apply(row, _model, body())
        if invalid:
            db.session.rollback()
            return fail('invalid_values', 400, fields=invalid,
                        detail=' '.join(e['message'] for e in invalid))
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            return fail('duplicate_or_invalid', 409, detail=str(exc.orig)[:200])
        return ok(_serialize(row))

    @bp.delete('/' + slug + '/<int:rid>', endpoint=endpoint + '_delete')
    @admin_required
    def _delete(rid, _model=model):
        row = db.session.get(_model, rid)
        if not row:
            return fail('not_found', 404)
        db.session.delete(row)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return fail('in_use', 409)
        return ok({'status': 'deleted'})

    if extra:
        extra(bp, model, slug)


# --------------------------------------------------------------------------- #
#  REGISTRY - one line per master table
# --------------------------------------------------------------------------- #
register('masters/zones', Zone, ('name', 'code', 'city', 'state'), ('name',))
register('masters/localities', Locality, ('name',), ('name',))
register('masters/areas', Area, ('name',), ('name',))
register('masters/buildings', Building, ('name',), ('name',))
register('masters/addresses', Address, ('name', 'city'), ())
register('masters/tax', TaxMaster, ('name',), ('name',), admin_write=True)
register('masters/addon-categories', AddonCategory, ('name',), ('name',))
register('masters/discount-reasons', DiscountReason, ('name', 'description'),
         ('name',), admin_write=True)
register('masters/message-templates', MessageTemplate,
         ('name', 'template_type'), ('name',))

register('staff/types', StaffType, ('name',), ('name',), admin_write=True)

register('expenses/categories', ExpenseCategory, ('name',), ('name',))
register('expenses/accounts', ExpenseAccount, ('name',), ('name',))
register('expenses/payees', ExpensePayee, ('name', 'mobile'), ('name',))
register('expenses', Expense, ('description', 'reference_no'), ('amount',),
         order_by='id')

register('inventory/vendors', Vendor, ('name', 'mobile', 'gstin'), ('name',))
register('inventory/products', Product, ('name', 'sku', 'hsn_code'), ('name',))
register('inventory/stock', Stock, (), (), order_by='id')
register('inventory/vendor-bills', VendorBill, ('bill_no',), (), order_by='id')

register('hr/attendance', Attendance, (), (), order_by='id')
register('hr/leaves', Leave, (), (), order_by='id')
register('hr/payroll', Payroll, (), (), order_by='id')
