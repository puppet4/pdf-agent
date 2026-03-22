export const API_BASE_URL = '';

export const getApiKey = () => {
  if (typeof window === 'undefined') {
    return import.meta.env.VITE_API_KEY || '';
  }
  return (
    window.__PDF_AGENT_API_KEY ||
    window.localStorage.getItem('pdf_agent_api_key') ||
    import.meta.env.VITE_API_KEY ||
    ''
  );
};
