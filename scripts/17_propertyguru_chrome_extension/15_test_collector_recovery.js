const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function run(state) {
  const timers = [];
  const next = {clicks: 0, click() { this.clicks += 1; }};
  const location = {
    href: 'https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang',
    assigned: null,
    assign(url) { this.assigned = url; }
  };
  const site = {
    pageNumber: () => 1,
    nextLink: () => next,
    pageUrl: page => `https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang/${page}`,
    matches: url => url.startsWith('https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang/')
  };
  const context = {
    LISTING_SITES: {propertyguru: site},
    location,
    document: {},
    window: {addEventListener() {}},
    chrome: {
      storage: {local: {
        get: async () => ({collectorState: state}),
        set: async ({collectorState}) => { state = collectorState; }
      }},
      runtime: {
        onMessage: {addListener() {}},
        sendMessage: async () => ({status: 'ready'})
      }
    },
    setInterval() {},
    setTimeout(fn, delay) { timers.push({fn, delay}); return timers.length; },
    clearTimeout() {},
    Date,
    Math,
    String
  };
  vm.runInNewContext(fs.readFileSync(__dirname + '/collector.js', 'utf8'), context);
  await new Promise(setImmediate);
  return {state: () => state, timers, next, location};
}

(async () => {
  const base = {
    active: true, sourceSite: 'propertyguru', runId: 'test',
    savedPages: [1], totalPages: 38,
    advanceFromPage: 1, advanceAt: Date.now() - 1
  };
  const recovered = await run({...base});
  assert.equal(recovered.timers.length, 1);
  await recovered.timers[0].fn();
  assert.equal(recovered.location.assigned,
    'https://www.propertyguru.com.sg/hdb-for-sale/in-sengkang/2');

  const legacy = await run({...base, advanceAt: undefined});
  assert.equal(legacy.state().active, false);
  assert.match(legacy.state().status, /Start a new run/);
  console.log('Collector recovery checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
