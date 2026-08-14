import { Outlet } from "react-router-dom";

import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import Footer from "../components/Footer";
import MobileSidebar from "../components/MobileSidebar";

function Base() {
  return (
    <>
      <Sidebar />
      <MobileSidebar />

      <div className="app-layout">
        <Topbar />

        <main className="main-content">
          <div className="content-wrapper">
            <Outlet />
          </div>
        </main>

        <Footer />
      </div>
    </>
  );
}

export default Base;