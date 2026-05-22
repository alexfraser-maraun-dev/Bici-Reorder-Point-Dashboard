import type { Metadata } from 'next'
import { Analytics } from '@vercel/analytics/next'
import './globals.css'

export const metadata: Metadata = {
  title: 'SKU Reorder Point & Desired Inventory Automation',
  description: 'Internal operations dashboard for retail inventory planning - review and manage SKU replenishment recommendations',
  generator: 'v0.app',
  icons: {
    icon: '/logo.svg',
    apple: '/logo.svg',
  },
}

import { Providers } from '@/components/providers'

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="bg-background">
      <body className="font-sans antialiased">
        <Providers>
          {children}
        </Providers>
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}
