// Version stamp shown next to the main header. Values are injected at build/dev-start by
// next.config.mjs from git + package.json (see its `env` block) — there is nothing to edit here.
// The SemVer base lives in package.json `version`; the commit SHA, summary, and date come from
// the HEAD commit the app was built from. Fallbacks keep things sane if the env wasn't injected.

export const APP_VERSION = process.env.NEXT_PUBLIC_APP_VERSION || '0.0.0'

// Short commit hash the build was produced from, e.g. "a1b2c3d".
export const APP_GIT_SHA = process.env.NEXT_PUBLIC_GIT_SHA || ''

// Subject line of the most recent commit — the "brief summary of the last changes".
export const APP_VERSION_SUMMARY = process.env.NEXT_PUBLIC_GIT_SUMMARY || ''

// Commit date (YYYY-MM-DD) of the build's HEAD commit.
export const APP_GIT_DATE = process.env.NEXT_PUBLIC_GIT_DATE || ''
