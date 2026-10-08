const assert = require('node:assert/strict');
require('./sites.js');

const site = globalThis.findListingSite(
  'https://www.propertyguru.com.sg/hdb-for-sale/2'
);
assert.equal(site.id, 'propertyguru');
assert.equal(site.pageNumber(site.startUrl), 1);
assert.equal(site.pageNumber(site.startUrl + '/2'), 2);
assert.equal(site.pageUrl(2), site.startUrl + '/2');
const filtered = 'https://www.propertyguru.com.sg/property-for-sale?page=3&propertyTypeGroup=H&propertyTypeCode=1R&propertyTypeCode=2A&freetext=Sengkang';
assert.equal(globalThis.findListingSite(filtered), site);
assert.equal(site.pageNumber(filtered), 3);
const first = new URL(site.startPageUrl(filtered));
assert.equal(first.searchParams.get('page'), '1');
assert.deepEqual(first.searchParams.getAll('propertyTypeCode'), ['1R', '2A']);
assert.equal(first.searchParams.get('freetext'), 'Sengkang');
const next = new URL(site.pageUrl(4, first.toString()));
assert.equal(next.searchParams.get('page'), '4');
assert.deepEqual(next.searchParams.getAll('propertyTypeCode'), ['1R', '2A']);
assert.equal(next.searchParams.get('freetext'), 'Sengkang');
assert.equal(globalThis.findListingSite('https://www.propertyguru.com.sg/property-for-sale?page=1'), undefined);
assert.equal(globalThis.findListingSite('https://www.propertyguru.com.sg/property-for-sale?propertyTypeGroup=C'), undefined);
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
    if (selector.startsWith('a[da-id^=')) return [{getAttribute: () => 'hui-pagination-btn-page-655'}];
    return [];
  },
  querySelector(selector) {
    if (selector === 'h1') return {innerText: '13,098 HDB Flats for Sale in Singapore'};
    return {href: '/hdb-for-sale/2'};
  }
};
const page = site.readPage(doc);
assert.equal(page.cards.length, 20);
assert.equal(page.advertisedCount, 13098);
assert.equal(page.totalPages, 655);
assert.ok(site.nextLink(doc));
console.log('Site adapter checks passed');
