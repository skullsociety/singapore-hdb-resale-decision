let busy = false;
let nextTimer = null;

async function advanceFromSavedPage(state, site, page) {
  if (nextTimer || state.advanceFromPage !== page || !state.advanceAt) return;
  nextTimer = setTimeout(async () => {
    nextTimer = null;
    try {
      const {collectorState: latest} = await chrome.storage.local.get('collectorState');
      if (!latest?.active || latest.runId !== state.runId ||
          site.pageNumber(location.href) !== page) return;
      const health = await chrome.runtime.sendMessage({type: 'HEALTH'});
      if (health?.status !== 'ready') {
        await updateStatus(state.runId, {
          active: false, status: 'Stopped: local receiver is offline. Restart it and start a new run.'
        });
        return;
      }
      const next = site.nextLink(document);
      if (!next) {
        await updateStatus(state.runId, {
          active: false, status: `Stopped: Next link missing after page ${page}`
        });
        return;
      }
      const destination = site.pageUrl(page + 1, state.searchUrl || site.startUrl);
      if (!destination || !site.matches(destination)) {
        await updateStatus(state.runId, {
          active: false, status: `Stopped: invalid next-page URL after page ${page}`
        });
        return;
      }
      location.assign(destination);
      setTimeout(async () => {
        if (site.pageNumber(location.href) === page) {
          await updateStatus(state.runId, {
            active: false, status: `Stopped: page did not advance after page ${page}`
          });
        }
      }, 10000);
    } catch (error) {
      await updateStatus(state.runId, {
        active: false, status: `Stopped while opening the next page: ${String(error)}`
      });
    }
  }, Math.max(0, state.advanceAt - Date.now()));
}

async function updateStatus(runId, values) {
  const {collectorState} = await chrome.storage.local.get('collectorState');
  if (collectorState?.runId !== runId) return;
  await chrome.storage.local.set({collectorState: {...collectorState, ...values}});
}

async function collectCurrentPage() {
  if (busy) return;
  busy = true;
  try {
    const {collectorState: state} = await chrome.storage.local.get('collectorState');
    const site = LISTING_SITES[state?.sourceSite];
    const page = site?.pageNumber(location.href);
    if (!state?.active || !page) return;
    if ((state.savedPages || []).includes(page)) {
      if (state.advanceFromPage !== page || !state.advanceAt) {
        await updateStatus(state.runId, {
          active: false,
          status: 'Stopped: this run predates the recovery update. Start a new run from page 1.'
        });
        return;
      }
      await advanceFromSavedPage(state, site, page);
      return;
    }

    const snapshot = site.readPage(document);
    const cards = snapshot.cards;
    if (!cards.length) {
      if (document.readyState !== 'complete') return;
      const started = Number(state.pageStartedAt || Date.now());
      if (!state.pageStartedAt) {
        await updateStatus(state.runId, {pageStartedAt: started});
      } else if (Date.now() - started > 15000) {
        await updateStatus(state.runId, {
          active: false, status: `Stopped: no listing cards on page ${page}; check for an access challenge`
        });
      }
      return;
    }

    const advertisedCount = state.advertisedCount || snapshot.advertisedCount;
    const totalPages = state.totalPages || snapshot.totalPages;
    if (!Number.isInteger(advertisedCount) || !Number.isInteger(totalPages) || totalPages < 1) {
      await updateStatus(state.runId, {active: false, status: 'Stopped: could not read count or page total'});
      return;
    }

    const result = await chrome.runtime.sendMessage({type: 'SAVE_PAGE', payload: {
      source_site: site.id,
      run_id: state.runId,
      page_number: page,
      total_pages: totalPages,
      advertised_count: advertisedCount,
      source_url: location.href,
      cards
    }});
    if (result?.error) throw new Error(result.error);
    const savedPages = [...new Set([...(state.savedPages || []), page])].sort((a, b) => a - b);
    const advanceAt = Date.now() + 3000 + Math.floor(Math.random() * 1001);
    await updateStatus(state.runId, {
      savedPages, advertisedCount, totalPages, pageStartedAt: null,
      advanceAt, advanceFromPage: page,
      status: `Saved page ${page}/${totalPages}; ${result.unique_listing_ids} unique listings. ` +
        `Opening page ${page + 1} shortly.`
    });
    if (page >= totalPages) {
      const ending = result.status === 'complete' ? 'Finished' : 'Stopped';
      await updateStatus(state.runId, {
        active: false,
        status: `${ending} ${savedPages.length}/${totalPages} pages; ` +
          `${result.unique_listing_ids} unique listings; ` +
          `${result.count_difference} difference from displayed count`
      });
      return;
    }
    await advanceFromSavedPage({...state, advanceAt, advanceFromPage: page}, site, page);
  } catch (error) {
    const {collectorState} = await chrome.storage.local.get('collectorState');
    if (collectorState?.active) {
      await updateStatus(collectorState.runId, {active: false, status: `Stopped: ${String(error)}`});
    }
  } finally {
    busy = false;
  }
}

chrome.runtime.onMessage.addListener(message => {
  if (message.type === 'PROCESS') collectCurrentPage();
  if (message.type === 'STOP' && nextTimer) {
    clearTimeout(nextTimer);
    nextTimer = null;
  }
});
window.addEventListener('popstate', () => setTimeout(collectCurrentPage, 1500));
setInterval(collectCurrentPage, 3000);
collectCurrentPage();
