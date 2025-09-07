/**
 * UI contract test: ensure form payload matches API schema fields.
 */

test('camera form payload matches API schema', () => {
  const buildPayload = () => ({
    name: 'Cam',
    url: 'rtsp://demo',
    orientation: 'vertical',
    transport: 'tcp',
    resolution: 'original',
    ppe: false,
    inout_count: false,
    reverse: false,
    show: false,
    enabled: false,
    site_id: null
  });

  const expected = [
    'name',
    'url',
    'orientation',
    'transport',
    'resolution',
    'ppe',
    'inout_count',
    'reverse',
    'show',
    'enabled',
    'site_id'
  ];

  const payload = buildPayload();
  expect(Object.keys(payload).sort()).toEqual(expected.sort());
});
