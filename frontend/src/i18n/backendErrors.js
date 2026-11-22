import i18n from './index';

// Backend (FastAPI) returns English `detail` strings. We never translate free
// text at runtime: only exact, known messages are mapped to translation keys
// (errors:backend.*). Unknown messages are shown untouched (safe fallback).
const EXACT = {
    'Ad not found': 'adNotFound',
    'Brand not found': 'brandNotFound',
    'Customer profile not found': 'profileNotFound',
    'Profile not found': 'profileNotFound',
    'Product not found': 'productNotFound',
    'Template not found': 'templateNotFound',
    'Search not found': 'searchNotFound',
    'Brand scrape not found': 'scrapeNotFound',
    'User not found': 'userNotFound',
    'Role not found': 'roleNotFound',
    'Cannot delete yourself': 'cannotDeleteSelf',
    'Could not validate credentials': 'invalidCredentials',
    'Email already registered': 'emailTaken',
    'Inactive user': 'inactiveUser',
    'User account is disabled': 'inactiveUser',
    'Incorrect email or password': 'badLogin',
    'Invalid refresh token': 'invalidRefresh',
    'Refresh token has expired': 'refreshExpired',
    'Only admins can create new users': 'adminOnlyCreate',
    'Superuser access required': 'superuserRequired',
    'Role already exists': 'roleExists',
    'Permission already exists': 'permissionExists',
    'Keyword already blacklisted': 'keywordBlacklisted',
    'Page already blacklisted': 'pageBlacklisted',
    'Gemini API key not configured': 'geminiNotConfigured',
    'R2 storage not configured': 'r2NotConfigured',
    'Could not upload file': 'uploadFailed',
    'File content does not match an image type': 'notAnImage',
    'Copy generation failed': 'copyFailed',
    'Deconstruction failed': 'deconstructFailed',
    'Reconstruction failed': 'reconstructFailed',
    'Field regeneration failed': 'regenerateFailed',
    'Failed to parse AI response as JSON': 'aiParseFailed',
    'Template has not been deconstructed yet': 'notDeconstructed',
    'Template has not been deconstructed yet. Run /deconstruct first.': 'notDeconstructed',
    'No ad IDs provided': 'noAdIds',
    'Meta Ads OAuth is not configured on this server.': 'metaNotConfigured',
    'No connected Meta Ads account. Connect and select one first.': 'metaNotConnected',
    'No connected Google Ads account. Connect one first.': 'googleNotConnected',
    'No connected TikTok Ads advertiser. Connect one first.': 'tiktokNotConnected',
    'Google Ads is not configured on this server (missing client ID/secret/developer token).': 'googleNotConfigured',
    'TikTok Ads is not configured. Complete TikTok developer app onboarding first.': 'tiktokNotConfigured',
    'Invalid URL. Must be a Facebook Ads Library URL with view_all_page_id or q= parameter.': 'invalidAdLibraryUrl',
    'Session expired. Please login again.': 'sessionExpired',
    'Failed to fetch user': 'fetchUserFailed',
    'Login failed': 'loginFailed',
    'Registration failed': 'registerFailed',
    'No connected Google Ads account.': 'googleNotConnectedShort',
    'No connected TikTok Ads advertiser.': 'tiktokNotConnectedShort',
    'No connected Meta Ads account. Connect Meta Ads in Facebook Campaigns first.': 'metaNotConnectedHint',
    'Google Ads is not configured on this server. Set GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, and GOOGLE_ADS_DEVELOPER_TOKEN.': 'googleNotConfigured',
    'TikTok Ads is not configured. Set TIKTOK_ADS_APP_ID and TIKTOK_ADS_APP_SECRET after your TikTok for Business developer app receives Marketing API access.': 'tiktokNotConfigured',
    'Google Ads API error.': 'googleApiError',
    'TikTok token refresh did not return an access token.': 'tiktokRefreshFailed',
    'No refresh token': 'noRefreshToken',
    'Failed to refresh token': 'refreshFailed',
    'No access token available': 'noAccessToken',
    // Browser-level network failures (Chrome / Firefox / Safari wording)
    'Failed to fetch': 'networkError',
    'NetworkError when attempting to fetch resource.': 'networkError',
    'Load failed': 'networkError',
};

const PATTERNS = [
    [/^Rate limit exceeded\. Try again in (\d+) seconds?\.?$/, 'rateLimit', (m) => ({ seconds: m[1] })],
    [/^TikTok Ads API request failed: (.*)$/s, 'tiktokRequestFailed', (m) => ({ message: m[1] })],
    [/^TikTok Ads returned an invalid response \(HTTP (\d+)\)\.$/, 'tiktokInvalidResponse', (m) => ({ status: m[1] })],
    [/^File too large\. Maximum size: ([\d.]+)MB$/, 'fileTooLarge', (m) => ({ size: m[1] })],
];

/** Translate a known backend/network error message; unknown text passes through. */
export function translateBackendMessage(message, fallbackKey) {
    if (!message) return fallbackKey ? i18n.t(fallbackKey) : '';
    const text = typeof message === 'string' ? message : message?.message || String(message);
    // The backend already speaks English: in English the message is shown verbatim.
    if ((i18n.resolvedLanguage || i18n.language || 'en-US').startsWith('en')) return text;
    if (EXACT[text]) return i18n.t(`errors:backend.${EXACT[text]}`);
    for (const [re, key, params] of PATTERNS) {
        const m = text.match(re);
        if (m) return i18n.t(`errors:backend.${key}`, params(m));
    }
    return text;
}

/** Convenience for catch blocks: translateError(err, 'auth:login.failed'). */
export function translateError(err, fallbackKey) {
    const raw = err?.message;
    if (!raw) return fallbackKey ? i18n.t(fallbackKey) : i18n.t('errors:generic');
    return translateBackendMessage(raw);
}
