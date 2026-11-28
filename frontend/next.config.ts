import type { NextConfig } from "next";

const apiProxyOrigin = process.env.API_PROXY_ORIGIN;

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The floating dev badge sits on top of the sidebar's Collapse button.
  devIndicators: false,
  async rewrites() {
    return apiProxyOrigin ? [{ source: "/api/:path*", destination: `${apiProxyOrigin}/api/:path*` }] : [];
  },
};

export default nextConfig;
