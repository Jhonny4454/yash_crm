function CustomerToolbar() {
  return (
    <div className="card mb-4 shadow-sm">
      <div className="card-body">

        <div className="row g-3 align-items-center">

          <div className="col-lg-4">
            <input
              type="text"
              className="form-control"
              placeholder="Search customer..."
            />
          </div>

          <div className="col-lg-3">
            <select className="form-select">
              <option>All Status</option>
              <option>Active</option>
              <option>Inactive</option>
            </select>
          </div>

          <div className="col-lg-3">
            <select className="form-select">
              <option>All Plans</option>
            </select>
          </div>

          <div className="col-lg-2 d-grid">
            <button className="btn btn-primary">
              <i className="fas fa-plus me-2"></i>
              Add Customer
            </button>
          </div>

        </div>

      </div>
    </div>
  );
}

export default CustomerToolbar;