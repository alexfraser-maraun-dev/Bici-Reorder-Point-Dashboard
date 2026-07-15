import { NextAuthOptions } from "next-auth"
import GoogleProvider from "next-auth/providers/google"

// Shared NextAuth config. Lives here (not in the route file) so both the NextAuth
// handler and the backend proxy route can import it for getServerSession().
export const authOptions: NextAuthOptions = {
  secret: process.env.NEXTAUTH_SECRET,
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID || "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET || "",
    }),
  ],
  callbacks: {
    async signIn({ user }) {
      // Only allow users with a @bici.cc email to sign in
      if (user.email && user.email.endsWith("@bici.cc")) {
        return true
      }
      return false
    },
  },
  pages: {
    // Optional: we can add a custom sign-in page later if needed
    // signIn: '/auth/signin',
  },
  session: {
    strategy: "jwt",
  },
}
