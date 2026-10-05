const RECEIVER = 'http://127.0.0.1:8771';
importScripts('sites.js');

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.type === 'HEALTH') {
    fetch(`${RECEIVER}/health`).then(response => response.json())
      .then(reply).catch(error => reply({error: String(error)}));
    return true;
  }
  if (message.type !== 'SAVE_PAGE') return false;
  (async () => {
    const {collectorState} = await chrome.storage.local.get('collectorState');
    const site = findListingSite(sender.tab?.url || '');
    if (!collectorState?.active || sender.tab?.id !== collectorState.tabId ||
        !site || site.id !== collectorState.sourceSite) {
      throw new Error('Collector is not active on a supported results tab');
    }
    const payload = message.payload;
    if (payload?.run_id !== collectorState.runId ||
        payload?.source_site !== site.id ||
        payload?.source_url !== sender.tab.url) {
      throw new Error('Page does not belong to the current run');
    }
    const response = await fetch(`${RECEIVER}/page`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Receiver HTTP ${response.status}`);
    return result;
  })().then(reply).catch(error => reply({error: String(error)}));
  return true;
});
