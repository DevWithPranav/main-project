import { createApiClient, keepMediaUrl, tokenExp, type FetchLike } from './client';

const jwt = (payload: object) => `h.${btoa(JSON.stringify(payload)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_')}.s`;

describe('tokens', () => {
  it('reads exp from a JWT and null from anything else', () => {
    expect(tokenExp(jwt({ sub: 'officer', exp: 1700000000 }))).toBe(1700000000);
    expect(tokenExp('mock.officer')).toBeNull();
    expect(tokenExp(null)).toBeNull();
  });

  it('refreshes once on a 401 and retries with the new token', async () => {
    let token = 'old';
    const seen: string[] = [];
    const fetch: FetchLike = async (_url, init) => {
      const auth = new Headers(init?.headers).get('Authorization') ?? '';
      seen.push(auth);
      return auth === 'Bearer new' ? new Response('{"username":"officer","role":"OFFICER"}', { status: 200 }) : new Response('{"detail":"expired"}', { status: 401 });
    };
    let lost = 0;
    const api = createApiClient({ fetch, getToken: () => token, refresh: async () => ((token = 'new'), true), onUnauthorized: () => lost++ });
    expect((await api.me()).username).toBe('officer');
    expect(seen).toEqual(['Bearer old', 'Bearer new']);
    expect(lost).toBe(0);
    // refresh fails: logged out, no retry loop
    token = 'old';
    seen.length = 0;
    const api2 = createApiClient({ fetch, getToken: () => token, refresh: async () => false, onUnauthorized: () => lost++ });
    await expect(api2.me()).rejects.toMatchObject({ status: 401 });
    expect(seen).toEqual(['Bearer old']);
    expect(lost).toBe(1);
  });

  it('keeps a signed media URL until it is about to expire', () => {
    const now = 1_000_000_000_000;
    const a = `/api/files/x.webm?exp=${now / 1000 + 3600}&sig=a`;
    const b = `/api/files/x.webm?exp=${now / 1000 + 3900}&sig=b`;
    expect(keepMediaUrl(null, a, now)).toBe(a);
    expect(keepMediaUrl(a, b, now)).toBe(a);
    expect(keepMediaUrl(a, b, now + 3400_000)).toBe(b); // < 5 min left
    expect(keepMediaUrl(a, '/api/files/y.webm?exp=1&sig=c', now)).toBe('/api/files/y.webm?exp=1&sig=c');
  });
});
