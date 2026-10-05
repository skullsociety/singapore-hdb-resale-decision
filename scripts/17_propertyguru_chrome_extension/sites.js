// Each approved site supplies its page selectors here. The collector stays generic.
globalThis.LISTING_SITES = Object.freeze({
  propertyguru: {
    id: 'propertyguru',
    name: 'PropertyGuru Sengkang HDB',
    startUrl: 'https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang',
    pageUrl(page) {
      return page === 1 ? this.startUrl : `${this.startUrl}/${page}`;
    },
    matches(url) {
      try {
        const parsed = new URL(url);
        return parsed.origin === 'https://www.propertyguru.com.sg' &&
          /^\/hdb-for-sale\/in-sengkang(?:\/\d+)?\/?$/.test(parsed.pathname);
      } catch { return false; }
    },
    pageNumber(url) {
      if (!this.matches(url)) return null;
      const match = new URL(url).pathname.match(/\/hdb-for-sale\/in-sengkang(?:\/(\d+))?\/?$/);
      return Number(match?.[1] || 1);
    },
    readPage(doc) {
      const cards = [...doc.querySelectorAll('div.listing-card-v2')].map(card => ({
        url: card.querySelector("a[href*='/listing/'][href*='for-sale-']")?.href || '',
        heading: card.querySelector('h3')?.innerText || '',
        text: card.innerText || ''
      })).filter(card => card.url.includes('/listing/hdb-for-sale-'));
      const heading = doc.querySelector('h1')?.innerText || '';
      const countMatch = heading.match(/([\d,]+)\s+HDB(?: Flats)? for Sale in Sengkang/i);
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
