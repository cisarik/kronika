(function (root) {
  const contract = Object.freeze({
    adapterVersion: 1,
    postRoots: Object.freeze(["article[data-testid='tweet']", "[data-kronika-post]"]),
    permalinkSelectors: Object.freeze([
      "a[href*='/status/'][role='link']",
      "a[href*='/status/'] time",
      "a[href*='/status/']",
    ]),
    composerRoots: Object.freeze([
      "[data-testid='tweetTextarea_0']",
      "[aria-label='Post your reply']",
      "[data-kronika-composer]",
    ]),
    composerFileInputs: Object.freeze([
      "input[data-kronika-composer-file]",
      "input[type='file'][accept*='image']",
      "input[type='file']",
    ]),
    composerToolbarSelectors: Object.freeze([
      "[data-testid='toolBar']",
      "[data-kronika-composer-toolbar]",
      "[role='toolbar']",
    ]),
    composerMediaButtonSelectors: Object.freeze([
      "[data-testid='fileInput']",
      "[aria-label='Add photos or video']",
      "[aria-label='Media']",
    ]),
    composerChromeSelectors: Object.freeze(["[data-kronika-composer-chrome]"]),
    composerTextRowSelectors: Object.freeze(["[data-kronika-composer-text-row]"]),
    contentDisclosureSelectors: Object.freeze([
      "[aria-label='Content disclosure']",
      "[data-kronika-content-disclosure]",
    ]),
    bookmarkSelectors: Object.freeze([
      "[data-testid='bookmark']",
      "[data-testid='removeBookmark']",
      "[aria-label='Bookmark']",
      "[aria-label='Add to Bookmarks']",
    ]),
    actionGroupSelectors: Object.freeze(["[role='group']"]),
    actionBarSignals: Object.freeze([
      "[data-testid='reply']",
      "[data-testid='retweet']",
      "[data-testid='like']",
    ]),
    shareSelectors: Object.freeze([
      "[data-testid='share']",
      "[aria-label='Share post']",
      "[aria-label='Share']",
    ]),
    mediaHostSelectors: Object.freeze([
      "[data-testid='tweetPhoto']",
      "[data-testid='videoPlayer']",
      "[data-testid='videoComponent']",
      "[data-kronika-media]",
    ]),
    tweetTextSelectors: Object.freeze([
      "[data-testid='tweetText']",
      "[data-kronika-tweet-text]",
    ]),
    requiredSignals: Object.freeze(["permalink", "composerFileInput"]),
  });
  if (typeof module !== "undefined" && module.exports) {
    module.exports = contract;
  }
  root.KronikaXAdapterContractV1 = contract;
})(typeof globalThis !== "undefined" ? globalThis : this);
