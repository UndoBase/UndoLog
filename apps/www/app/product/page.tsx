"use client";

import ConceptScreens from "@/components/sections";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";

export default function ProductPage() {
  return (
    <>
      <Navbar />
      <main style={{ paddingTop: 64 }}>
        <ConceptScreens />
      </main>
      <Footer />
    </>
  );
}
