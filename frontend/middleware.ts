export { default } from "next-auth/middleware"

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api/auth (NextAuth API endpoints)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico, logo.svg (static assets)
     */
    '/((?!api/auth|_next/static|_next/image|favicon.ico|logo.svg).*)',
  ],
}
