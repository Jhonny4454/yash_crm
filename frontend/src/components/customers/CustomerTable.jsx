function CustomerTable() {

  const customers = [
    {
      id: 1,
      name: "Rahul Sharma",
      mobile: "9876543210",
      plan: "100 Mbps",
      status: "Active",
      balance: "₹0"
    },
    {
      id: 2,
      name: "Amit Patil",
      mobile: "9876500000",
      plan: "50 Mbps",
      status: "Inactive",
      balance: "₹550"
    }
  ];

  return (
    <div className="card shadow-sm">

      <div className="table-responsive">

        <table className="table table-hover align-middle">

          <thead className="table-light">

            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Mobile</th>
              <th>Plan</th>
              <th>Status</th>
              <th>Balance</th>
              <th>Actions</th>
            </tr>

          </thead>

          <tbody>

            {customers.map((customer) => (

              <tr key={customer.id}>

                <td>{customer.id}</td>

                <td>{customer.name}</td>

                <td>{customer.mobile}</td>

                <td>{customer.plan}</td>

                <td>

                  <span
                    className={`badge ${
                      customer.status === "Active"
                        ? "bg-success"
                        : "bg-danger"
                    }`}
                  >
                    {customer.status}
                  </span>

                </td>

                <td>{customer.balance}</td>

                <td>

                  <button className="btn btn-sm btn-primary me-2">
                    <i className="fas fa-edit"></i>
                  </button>

                  <button className="btn btn-sm btn-danger">
                    <i className="fas fa-trash"></i>
                  </button>

                </td>

              </tr>

            ))}

          </tbody>

        </table>

      </div>

    </div>
  );
}

export default CustomerTable;