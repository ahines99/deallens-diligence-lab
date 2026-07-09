/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Surface the API base URL to the client at build time.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
};

export default nextConfig;
