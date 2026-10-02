import { createContext, useContext, useEffect, useState } from "react";
import { api, type Brand } from "./api";

export const DEFAULT_BRAND: Brand = {
  product_name: "Content Studio",
  org_name: "Your Brand",
  logo_initials: "CS",
  public_url: "",
  colors: {},
  fonts: {},
};

const Ctx = createContext<Brand>(DEFAULT_BRAND);

export function BrandProvider({ children }: { children: React.ReactNode }) {
  const [brand, setBrand] = useState<Brand>(DEFAULT_BRAND);
  useEffect(() => {
    api.brand().then((b) => {
      setBrand(b);
      document.title = b.product_name;
      const accent = b.colors?.accent_glow;
      if (accent) {
        document.documentElement.style.setProperty("--accent", accent);
        document.documentElement.style.setProperty("--good", accent);
      }
    }).catch(() => {});
  }, []);
  return <Ctx.Provider value={brand}>{children}</Ctx.Provider>;
}

export const useBrand = () => useContext(Ctx);
