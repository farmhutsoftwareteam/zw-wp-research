// Vercel Edge Middleware — HTTP Basic Auth on the engagement-kit paths.
//
// Public WP directory at / stays open. The /pitch, /review, /share paths
// (the lead-gen data with personal contact info) require a username + PIN.
//
// Set the PIN in the Vercel dashboard:
//   Project → Settings → Environment Variables → Add
//     Name:  ENGAGEMENT_PIN
//     Value: <your-shared-pin>
//   then redeploy.
//
// Username is fixed to "mikey" — change `USER` below if you want a different name.

export const config = {
  matcher: ['/pitch/:path*', '/review/:path*', '/share/:path*'],
};

const USER = 'mikey';

export default function middleware(request) {
  const expected = process.env.ENGAGEMENT_PIN;
  if (!expected) {
    return new Response(
      'Engagement kit is not configured — set ENGAGEMENT_PIN in Vercel env vars.',
      { status: 503 },
    );
  }
  const auth = request.headers.get('authorization') || '';
  if (auth.startsWith('Basic ')) {
    const [u, p] = atob(auth.slice(6)).split(':');
    if (u === USER && p === expected) {
      return; // pass through
    }
  }
  return new Response('Authentication required.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="ZW WP engagement kit", charset="UTF-8"',
      'Cache-Control': 'no-store',
    },
  });
}
