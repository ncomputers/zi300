/**
 * @jest-environment jsdom
 */

test('leaves existing src intact', () => {
  document.body.innerHTML = '<img class="feed-img" src="/api/cameras/1/mjpeg">';
  globalThis.__TEST__ = true;
  const { initMjpegFeeds } = require('../static/js/mjpeg_feed.js');
  const img = document.querySelector('img.feed-img');
  global.fetch = jest.fn(() => Promise.resolve());
  initMjpegFeeds(document);
  expect(fetch).not.toHaveBeenCalled();
  expect(img.getAttribute('src')).toBe('/api/cameras/1/mjpeg');
});
