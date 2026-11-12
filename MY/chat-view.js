// Preserve the visible control/figure even when a filter changes chart heights.
function key(node) {
  const message=node.closest('[data-message-id]')?.dataset.messageId;
  const chart=node.closest('[data-chart-id]')?.dataset.chartId || '';
  const control=node.dataset.focusKey || node.dataset.uiKey || node.dataset.uiScroll || '';
  return message && control ? `${message}/${chart}/${control}` : null;
}
export function captureChatView(root,scroll) {
  const active=document.activeElement;
  const focus=root.contains(active) ? key(active) : null;
  const top=scroll.getBoundingClientRect().top;
  const candidates=[...root.querySelectorAll('[data-focus-key], [data-chart-id], [data-message-id]')];
  const dashboard=[...root.querySelectorAll('.message-plots, .message-table')].find(node=>{const y=node.getBoundingClientRect().top;return y>=top-1 && y<=top+80;});
  const anchor=(focus && active.getBoundingClientRect().top>=top ? active : null) || dashboard || candidates.find(node=>node.getBoundingClientRect().top>=top && node.getBoundingClientRect().top<top+scroll.clientHeight);
  return {focus,anchor,anchorKey:anchor ? key(anchor) : null,anchorTop:anchor?.getBoundingClientRect().top,scrollTop:scroll.scrollTop};
}
export function restoreChatView(root,scroll,saved) {
  const keyed=[...root.querySelectorAll('[data-focus-key], [data-ui-key]')];
  const focus=saved.focus ? keyed.find(node=>key(node)===saved.focus) : null;
  if (focus && !focus.disabled) focus.focus({preventScroll:true});
  const anchor=saved.anchor?.isConnected ? saved.anchor : (saved.anchorKey ? keyed.find(node=>key(node)===saved.anchorKey) : null);
  scroll.scrollTop=saved.scrollTop;
  if (anchor && saved.anchorTop!==undefined) scroll.scrollTop += anchor.getBoundingClientRect().top-saved.anchorTop;
}
