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
  // retry a non-idempotent method when the connection was NEVER established
  // (ECONNREFUSED/DNS during cold start) — never on a mid-flight reset, which could
  // double-apply a write like a price push.
  const idempotent = method === "GET" || method === "HEAD"
  const neverConnected = (err: unknown): boolean => {
    const code = (err as { cause?: { code?: string } } | null)?.cause?.code
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
    return NextResponse.json({ detail: "Backend unreachable" }, { status: 502 })
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
