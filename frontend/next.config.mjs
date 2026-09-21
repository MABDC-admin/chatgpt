/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Proxy API calls to the production backend during local development.
  // Uses API_PROXY_TARGET (server-only) so the browser always calls /api
  // on localhost, avoiding CORS entirely.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET || "https://chat.mabdc.com";
    return [
      {
        source: "/api/:path*",
        destination: `${target}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
