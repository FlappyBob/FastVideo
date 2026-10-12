/** A transport failure, before the API returned any HTTP response. */
export class ApiConnectionError extends Error {
  constructor(readonly url: string) {
    super(`Could not reach the Studio API at ${url}. Check API Server Base URL in Settings and the API port forwarding.`);
    this.name = 'ApiConnectionError';
  }
}
