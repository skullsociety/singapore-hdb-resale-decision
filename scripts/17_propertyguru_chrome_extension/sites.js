// Each approved site supplies its page selectors here. The collector stays generic.
globalThis.LISTING_SITES = Object.freeze({
  propertyguru: {
    id: 'propertyguru',
    name: 'PropertyGuru Singapore HDB',
    startUrl: 'https://www.propertyguru.com.sg/hdb-for-sale',
    startPageUrl(url) {
      if (!this.matches(url)) throw new Error('Unsupported PropertyGuru results URL');
      const parsed = new URL(url);
      if (parsed.pathname === '/property-for-sale') {
        parsed.searchParams.set('page', '1');
        return parsed.toString();
      }
      parsed.pathname = '/hdb-for-sale';
      return parsed.toString();
    },
    pageUrl(page, searchUrl = this.startUrl) {
      if (!Number.isInteger(page) || page < 1 || !this.matches(searchUrl)) return null;
      const parsed = new URL(searchUrl);
      if (parsed.pathname === '/property-for-sale') {
        parsed.searchParams.set('page', String(page));
      } else {
        parsed.pathname = page === 1 ? '/hdb-for-sale' : `/hdb-for-sale/${page}`;
      }
      return parsed.toString();
    },
    matches(url) {
      try {
        const parsed = new URL(url);
        if (parsed.origin !== 'https://www.propertyguru.com.sg') return false;
        if (/^\/hdb-for-sale(?:\/\d+)?\/?$/.test(parsed.pathname)) return true;
        return parsed.pathname === '/property-for-sale' &&
          parsed.searchParams.get('propertyTypeGroup')?.toUpperCase() === 'H' &&
          (!parsed.searchParams.has('page') || /^[1-9]\d*$/.test(parsed.searchParams.get('page')));
      } catch { return false; }
    },
    pageNumber(url) {
      if (!this.matches(url)) return null;
      const parsed = new URL(url);
      if (parsed.pathname === '/property-for-sale') {
        return Number(parsed.searchParams.get('page') || 1);
      }
      const match = parsed.pathname.match(/\/hdb-for-sale(?:\/(\d+))?\/?$/);
      return Number(match?.[1] || 1);
    },
    readPage(doc) {
      const cards = [...doc.querySelectorAll('div.listing-card-v2')].map(card => ({
        url: card.querySelector("a[href*='/listing/'][href*='for-sale-']")?.href || '',
        heading: card.querySelector('h3')?.innerText || '',
        text: card.innerText || ''
      })).filter(card => card.url.includes('/listing/hdb-for-sale-'));
      const heading = doc.querySelector('h1')?.innerText || '';
      const countMatch = heading.match(/([\d,]+)\s+HDB(?:\s+Flats?)?\s+for Sale(?:\s+in\s+Singapore)?/i);
      const advertisedCount = Number(countMatch?.[1]?.replaceAll(',', ''));
      const pageNumbers = [...doc.querySelectorAll('a[da-id^="hui-pagination-btn-page-"]')]
        .map(link => Number(link.getAttribute('da-id')?.split('-').at(-1)))
        .filter(Number.isInteger);
      const totalPages = Math.max(0, ...pageNumbers,
        cards.length && advertisedCount ? Math.ceil(advertisedCount / cards.length) : 0);
      return {cards, advertisedCount, totalPages};
    },
    nextLink(doc) {
      return doc.querySelector("a[da-id='hui-pagination-btn-next']");
    }
  }
});

globalThis.findListingSite = url =>
  Object.values(globalThis.LISTING_SITES).find(site => site.matches(url));
