import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

// Derive the version stamp from git at build/dev-start time. Evaluated whenever this config
// loads (each `next dev` / `next build`), so the header reflects the commit it was built from.
// All git calls are best-effort — fall back gracefully where git/history is unavailable (CI
// shallow clones, exported tarballs, etc.).
function git(cmd) {
  try {
    return execSync(`git ${cmd}`, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return ''
  }
}

function readVersion() {
  try {
    return JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).version || '0.0.0'
  } catch {
    return '0.0.0'
  }
}

const APP_VERSION = readVersion()
const GIT_SHA = git('rev-parse --short HEAD')
const GIT_SUMMARY = git('log -1 --pretty=%s')
const GIT_DATE = git('log -1 --pretty=%cs') // YYYY-MM-DD

/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  env: {
    NEXT_PUBLIC_APP_VERSION: APP_VERSION,
    NEXT_PUBLIC_GIT_SHA: GIT_SHA,
    NEXT_PUBLIC_GIT_SUMMARY: GIT_SUMMARY,
    NEXT_PUBLIC_GIT_DATE: GIT_DATE,
  },
}

export default nextConfig
