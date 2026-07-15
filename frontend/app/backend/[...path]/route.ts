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

  const method = req.method.toUpperCase()
  const init: RequestInit = { method, headers, redirect: "manual" }
  // Request bodies here are small (JSON, or the capped SKU CSV upload) — buffering
  // them is simpler and safe. Only the RESPONSE side needs streaming.
  if (method !== "GET" && method !== "HEAD") {
    init.body = await req.arrayBuffer()
  }

  let upstream: Response
  try {
    upstream = await fetch(target, init)
  } catch {
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
