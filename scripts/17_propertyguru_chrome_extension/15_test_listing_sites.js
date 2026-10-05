const assert = require('node:assert/strict');
require('./sites.js');

const site = globalThis.findListingSite(
  'https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang/2'
);
assert.equal(site.id, 'propertyguru');
assert.equal(site.pageNumber(site.startUrl), 1);
assert.equal(site.pageNumber(site.startUrl + '/2'), 2);
assert.equal(site.pageUrl(2), site.startUrl + '/2');
assert.equal(globalThis.findListingSite('https://example.org/search'), undefined);

const doc = {
  querySelectorAll(selector) {
    if (selector === 'div.listing-card-v2') return Array(20).fill({
      innerText: 'S$ 600,000',
      querySelector(inner) {
        if (inner === 'h3') return {innerText: '100 Sengkang East Way'};
        return {href: 'https://www.propertyguru.com.sg/listing/hdb-for-sale-test-100'};
      }
    });
    if (selector.startsWith('a[da-id^=')) return [{getAttribute: () => 'hui-pagination-btn-page-38'}];
    return [];
  },
  querySelector(selector) {
    if (selector === 'h1') return {innerText: '757 HDB for Sale in Sengkang'};
    return {href: '/hdb-for-sale/in-sengkang/2'};
  }
};
const page = site.readPage(doc);
assert.equal(page.cards.length, 20);
assert.equal(page.advertisedCount, 757);
assert.equal(page.totalPages, 38);
assert.ok(site.nextLink(doc));
console.log('Site adapter checks passed');
