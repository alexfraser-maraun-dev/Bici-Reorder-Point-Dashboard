import { NextRequest, NextResponse } from "next/server"
import { getServerSession } from "next-auth/next"
import { authOptions } from "@/lib/auth"

// Server-side proxy: the browser calls same-origin `/backend/...` and this handler
// forwards the request to the FastAPI backend, injecting a shared secret the browser
// never sees. Every request is gated on a valid NextAuth session first.
//
// Responses are streamed straight through (upstream.body -> NextResponse) rather than
// buffered/re-serialized, so a 1MB payload doesn't sit in the Node heap.

const BACKEND_URL = (process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/+$/, "")
const SHARED_SECRET = process.env.BACKEND_SHARED_SECRET || ""

// Node runtime (not edge): getServerSession + streaming fetch to the backend.
export const runtime = "nodejs"
// This route is inherently per-request; never let Next try to cache it.
export const dynamic = "force-dynamic"

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const session = await getServerSession(authOptions)
  if (!session) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 })
  }

  const { path } = await ctx.params
  const target = `${BACKEND_URL}/${(path || []).join("/")}${req.nextUrl.search}`

  const headers = new Headers()
  const contentType = req.headers.get("content-type")
  if (contentType) headers.set("content-type", contentType)
  if (SHARED_SECRET) headers.set("x-internal-secret", SHARED_SECRET)
  // Identity for the backend's feature-access checks. Read from the server-side
  // session, never from the incoming request, so the browser cannot spoof it.
  const email = session.user?.email
  if (email) headers.set("x-user-email", email)

  const method = req.method.toUpperCase()
  const init: RequestInit = { method, headers, redirect: "manual" }
  // Request bodies here are small (JSON, or the capped SKU CSV upload) — buffering
  // them is simpler and safe. Only the RESPONSE side needs streaming. Buffering also
  // makes the body reusable across the connection retry below (a stream would not be).
  if (method !== "GET" && method !== "HEAD") {
    init.body = await req.arrayBuffer()
  }

  // The backend runs on Render's free tier and can spin down / briefly reset, so a
  // single fetch occasionally throws before it ever reaches FastAPI (the classic
  // "Backend unreachable" on a DELETE/POST). Retry, but stay safe on mutations: only
  // retry a NON-idempotent method when the connection was NEVER established
  // (ECONNREFUSED/DNS during cold start) — never on a mid-flight reset, which could
  // double-apply a write like a price push.
  //
  // Idempotent means "repeating it lands on the same final state" (RFC 9110), which
  // covers DELETE and PUT as well as reads — not just the safe methods. Every DELETE
  // this API exposes removes one identified resource, so a repeat is a no-op. Leaving
  // them out gave a Release/Unpark exactly one attempt where a page load got three,
  // so an idle tab hitting a cold backend failed outright on a button but recovered
  // silently on a refresh. POST and PATCH stay strictly single-attempt.
  const idempotent = method === "GET" || method === "HEAD" ||
    method === "DELETE" || method === "PUT"
  const causeCode = (err: unknown): string | undefined =>
    (err as { cause?: { code?: string } } | null)?.cause?.code
  const neverConnected = (err: unknown): boolean => {
    const code = causeCode(err)
    return code === "ECONNREFUSED" || code === "ENOTFOUND" || code === "EAI_AGAIN"
  }
  const MAX_ATTEMPTS = 3
  let upstream: Response | null = null
  let lastErr: unknown = null
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      upstream = await fetch(target, init)
      break
    } catch (err) {
      lastErr = err
      if (attempt >= MAX_ATTEMPTS || !(idempotent || neverConnected(err))) break
      await new Promise((r) => setTimeout(r, attempt * 400))
    }
  }
  if (!upstream) {
    console.error(`[backend-proxy] ${method} ${target} unreachable:`, lastErr)
    // Name the actual failure. "Backend unreachable" on its own sent someone hunting
    // through application code for a bug that was really a stopped/cold API process.
    const code = causeCode(lastErr)
    const detail = code === "ECONNREFUSED"
      ? "The API server is not running or is still starting up. Try again in a moment."
      : code === "ENOTFOUND" || code === "EAI_AGAIN"
        ? "The API server address could not be resolved."
        : `The connection to the API server failed${code ? ` (${code})` : ""}.`
    return NextResponse.json(
      { detail: `Backend unreachable. ${detail}`, code: code ?? null },
      { status: 502 },
    )
  }

  const respHeaders = new Headers()
  const ct = upstream.headers.get("content-type")
  if (ct) respHeaders.set("content-type", ct)
  const cd = upstream.headers.get("content-disposition")
  if (cd) respHeaders.set("content-disposition", cd)

  return new NextResponse(upstream.body, { status: upstream.status, headers: respHeaders })
}

export const GET = proxy
export const POST = proxy
export const PUT = proxy
export const DELETE = proxy
export const PATCH = proxy
