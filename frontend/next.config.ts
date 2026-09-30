import path from "node:path";
import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// Single source of env vars: the repo-root sehat-ai/.env (see .env.example).
loadEnvConfig(path.resolve(process.cwd(), ".."));

const nextConfig: NextConfig = {};

export default nextConfig;
