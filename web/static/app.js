'use strict';

const $ = id => document.getElementById(id);
const state = {issues: [], selected: null, run: null, rendered: '', completed: null};
const terminal = run => !run || run.finished;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function api(path, payload) {
  const response = await fetch(path, payload ? {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Operator-Token': state.config.token},
    body: JSON.stringify(payload)
  } : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}

function report(error) {
  $('error').textContent = error?.message || '';
  $('error').hidden = !error;
}

function bind(id, event, callback) {
  $(id).addEventListener(event, async e => {
    try {
      report();
      await callback(e);
    } catch (error) {
      report(error);
    }
  });
}

function labels(issue) {
  const group = node('div', undefined, 'labels');
  for (const label of issue.labels || []) group.append(node('span', label, 'label'));
  return group;
}

function issueStatus(issue) {
  return node('span', issue.state === 'closed' ? '◉ Closed' : '◉ Open',
    'issue-state ' + (issue.state === 'closed' ? 'closed' : 'open'));
}

function renderList() {
  const filter = $('search').value.trim().toLowerCase();
  const matches = state.issues.filter(issue => (
    issue.title.toLowerCase().includes(filter) || `#${issue.id}`.includes(filter)
  ));
  $('count').textContent = matches.length;
  const list = document.createDocumentFragment();
  for (const issue of matches) {
    const row = node('button', undefined, 'issue-item');
    row.type = 'button';
    row.setAttribute('aria-current', String(issue.id === state.selected));
    const meta = node('div', undefined, 'meta');
    meta.append(issueStatus(issue), document.createTextNode(` · #${issue.id}`));
    row.append(meta, node('strong', issue.title), labels(issue));
    row.addEventListener('click', () => selectIssue(issue.id).catch(report));
    list.append(row);
  }
  if (!matches.length) list.append(node('p', 'No matching issues.', 'empty'));
  $('issues').replaceChildren(list);
}

async function selectIssue(id, refresh = false) {
  state.selected = id;
  if (!refresh) {
    state.detail = null;
    $('issue').replaceChildren(node('p', 'Loading issue…', 'empty'));
    report();
  }
  renderList();
  controls();
  const issue = await api('/api/issues/' + id);
  if (state.selected !== id) return;
  state.detail = issue;
  const heading = node('header', undefined, 'issue-heading');
  const title = node('h2', issue.title);
  title.id = 'issue-title';
  heading.append(node('div', `ISSUE #${issue.id}`, 'eyebrow'), title, issueStatus(issue), labels(issue));
  const comments = issue.comments || [];
  $('issue').replaceChildren(heading, node('p', issue.body || 'No description provided.', 'issue-body'),
    node('h3', `Comments · ${comments.length}`, 'comments-title'));
  for (const comment of comments) {
    const section = node('article', undefined, 'comment');
    section.append(node('strong', comment.author || 'Contributor'), node('p', comment.body || ''));
    $('issue').append(section);
  }
  controls();
}

async function loadIssues(autoSelect = false) {
  const filter = $('filter').value;
  const data = await api('/api/issues?state=' + encodeURIComponent(filter));
  if (filter !== $('filter').value) return;
  state.issues = data.issues;
  renderList();
  if (autoSelect && !state.selected && state.issues.length) await selectIssue(state.issues[0].id);
}

function controls() {
  $('start').disabled = !state.detail || state.detail.state === 'closed' || !terminal(state.run) || state.launching;
}

function renderEvidence(container, evidence) {
  if (evidence.root_cause) container.append(node('h3', 'Root cause'), node('p', evidence.root_cause));
  if (evidence.fix_plan) container.append(node('h3', 'Fix plan'), node('p', evidence.fix_plan));
  if (evidence.tests?.length) {
    container.append(node('p', 'Test evidence'), node('pre', JSON.stringify(evidence.tests, null, 2)));
  }
  if (evidence.diff) container.append(node('p', 'Proposed diff'), node('pre', evidence.diff));
}

function decisionButton(run, text, answer) {
  const button = node('button', text, answer === 'yes' ? 'primary' : '');
  button.type = 'button';
  button.addEventListener('click', async () => {
    const buttons = button.parentNode.querySelectorAll('button');
    buttons.forEach(item => {item.disabled = true;});
    try {
      report();
      await api('/api/decision', {id: run.id, proposal: run.review.id, answer});
      await poll();
    } catch (error) {
      buttons.forEach(item => {item.disabled = false;});
      report(error);
    }
  });
  return button;
}

function renderRun(run) {
  const serialized = JSON.stringify(run);
  if (serialized === state.rendered) return;
  state.rendered = serialized;
  const badge = $('status');
  badge.dataset.state = run?.status || 'IDLE';
  badge.textContent = (run?.status || 'Idle').replaceAll('_', ' ').toLowerCase()
    .replace(/^./, first => first.toUpperCase());
  if (!run) return;
  const activity = $('activity');
  activity.replaceChildren(node('p', `Issue #${run.issue} · ${run.phase}`, 'meta'));
  if (run.triage_summary) activity.append(node('p', run.triage_summary));
  for (const entry of run.activity) {
    const event = node('div', undefined, 'event' + (entry.ok === false ? ' failed' : ''));
    event.append(node('span', entry.ok === false ? '×' : '✓'),
      document.createTextNode(entry.tool.replaceAll('_', ' ')));
    activity.append(event);
  }
  if (run.summary) activity.append(node('p', run.summary));
  if (!run.activity.length && !run.summary) activity.append(node('p', 'Agent is working…', 'muted'));
  const review = $('review');
  review.replaceChildren();
  review.hidden = !run.review && !run.evidence;
  if (run.review) {
    review.append(node('h3', run.review.diff ? 'Review proposed code fix' : 'Review proposed issue update'),
      node('p', run.review.explanation || 'Inspect the exact request before approving.'),
      node('pre', JSON.stringify(run.review.request, null, 2)));
    renderEvidence(review, run.review);
    const actions = node('div', undefined, 'actions');
    actions.append(decisionButton(run, 'Approve change', 'yes'), decisionButton(run, 'Reject', 'no'));
    review.append(actions);
  } else if (run.evidence) {
    review.append(node('h3', run.status === 'FAILED' ? 'Unverified fix attempt' : 'Code fix and test evidence'));
    renderEvidence(review, run.evidence);
    if (run.publication) review.append(node('pre', JSON.stringify(run.publication, null, 2)));
  }
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    ({run: state.run} = await api('/api/run'));
    renderRun(state.run);
    controls();
    if (state.run?.finished && state.run.id !== state.completed) {
      state.completed = state.run.id;
      await loadIssues();
      if (state.selected === state.run.issue) await selectIssue(state.selected, true);
    }
  } finally {
    state.polling = false;
  }
}

async function start() {
  state.config = await api('/api/config');
  const local = state.config.backend === 'local';
  $('connection').textContent = local ? 'Local sandbox' : 'GitHub mode';
  $('repo').textContent = local ? '⌘  Sample repository' : '⌘  ' + state.config.scope;
  $('workflow').textContent = state.config.offline
    ? 'Offline mode runs triage only. Start in GitHub mode with OpenAI credentials to generate a code fix.'
    : 'Triage, investigate, and test a fix in one run. Review each proposed write.';
  bind('search', 'input', renderList);
  bind('filter', 'change', () => loadIssues());
  bind('refresh', 'click', async () => {
    await loadIssues();
    if (state.selected) await selectIssue(state.selected, true);
  });
  bind('launch', 'submit', async event => {
    event.preventDefault();
    state.launching = true;
    controls();
    try {
      await api('/api/run', {issue: state.selected});
      await poll();
    } finally {
      state.launching = false;
      controls();
    }
  });
  await loadIssues(true);
  await poll();
  setInterval(() => poll().catch(report), 850);
  setInterval(async () => {
    try {
      await loadIssues();
      const summary = state.issues.find(issue => issue.id === state.selected);
      if (summary && summary.version !== state.detail?.version) await selectIssue(summary.id, true);
    } catch (error) {
      report(error);
    }
  }, 20000);
}

start().catch(report);
