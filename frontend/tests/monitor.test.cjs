const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

// Minimal DOM to exercise the real asynchronous panel rendering without dependencies.
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.dataset = {}; this.open = false;}
  append(...items) {for (const item of items) this.children.push(...(item.tag === 'fragment' ? item.children : [item]));}
  replaceChildren(...items) {this.children = []; this.append(...items);}
  querySelectorAll(tag) {return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]);}
}
function setup() {
  const elements = Object.fromEntries(['deal-control', 'page-deal', 'deal-title'].map(id => [id, new Element('div')]));
  const context = vm.createContext({
    state: {requests: [{id: 'a', data: {subjectRef: 'a'}}], demoSnapshot: {dealId: 'other'}, workflows: [], processes: [], sources: []},
    $: id => elements[id], node: tag => new Element(tag),
    document: {createDocumentFragment: () => new Element('fragment')},
    window: {scrollX: 0, scrollY: 800, scrollTo({left, top}) {this.scrollX = left; this.scrollY = top;}},
    api: async () => [], resultMetric: () => new Element('div'), detailEntry: () => {},
    fieldTitle: x => x, badge: () => new Element('span'),
  });
  vm.runInContext(fs.readFileSync('frontend/monitor.js', 'utf8'), context);
  vm.runInContext(`monitor.selected = 'a'; summarize = request => ({request, context: {}, execution: {}, checks: {}, items: [], stages: [], reasons: [], total: 0, count: 0});`, context);
  return {context, elements, load: () => vm.runInContext('loadDealPanel()', context)};
}
test('refresh retains expanded details and scroll, including toggles made during loading', async () => {
  const {context, elements, load} = setup();
  await load();
  const panel = elements['deal-control'];
  const original = panel.children[0];
  let resolve;
  context.api = () => new Promise(done => {resolve = done;});
  const pending = load();
  assert.equal(panel.children[0], original, 'existing content stays visible while fetching');
  panel.querySelectorAll('details')[0].open = true;
  panel.querySelectorAll('details')[2].open = true;
  context.window.scrollY = 1200;
  resolve([]);
  await pending;
  assert.deepEqual(panel.querySelectorAll('details').map(item => item.open), [true, false, true]);
  assert.equal(context.window.scrollY, 1200);
});
test('expanded state does not leak into another deal', async () => {
  const {context, elements, load} = setup();
  await load();
  elements['deal-control'].querySelectorAll('details')[0].open = true;
  vm.runInContext("state.requests = [{id: 'b', data: {subjectRef: 'b'}}]; monitor.selected = 'b';", context);
  await load();
  assert.ok(elements['deal-control'].querySelectorAll('details').every(item => !item.open));
});
