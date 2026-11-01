let processResultEpoch = 0;
async function loadProcessResult() {
  const epoch = ++processResultEpoch;
  try {
    const data = await api("/dashboard");
    if (epoch !== processResultEpoch) return;
    state.demoSnapshot = data; state.demoResultError = null;
    const request = state.requests.find((r) => r.id === data.dealId);
    if (request) {try {await loadAudit(request.id, true);} catch {}}
  } catch (error) {
    if (epoch !== processResultEpoch) return;
    state.demoResultError = error.message;
  }
  renderProcessResults();
}
function resultMetric(title, value, note = "") {
  const box = node("div"); box.append(node("span", title, "result-label"), node("strong", value));
  if (note) box.append(node("span", note, "result-note"));
  return box;
}
function renderProcessResults() {
  const data = state.demoSnapshot;
  for (const section of document.querySelectorAll(".process-outcome")) {
    const key = pretty([data, state.demoResultError, state.requests, monitor.audit.get(data?.dealId)?.items, monitor.audit.get(data?.dealId)?.error, state.workflows.map((w) => [w.workflowId, w.processId])]);
    if (section.resultKey === key) continue;
    section.resultKey = key;
    section.replaceChildren();
    const workflowId = data?.workflowId || state.requests.find((r) => r.id === data?.dealId)?.data.dealType;
    const integration = state.workflows.find((w) => w.workflowId === workflowId);
    section.hidden = !state.demoResultError && (!data || integration?.processId !== section.dataset.processId);
    for (const row of section.parentElement.querySelectorAll(".process-request-list [data-request-id]")) row.hidden = !section.hidden && row.dataset.requestId === data?.dealId;
    if (section.hidden) continue;
    if (state.demoResultError || !data || data.status === "UNAVAILABLE" || data.error) {
      section.append(node("h3", "Результат конкретной сделки"), node("p", "Не удалось получить состояние сделки. Подтверждения и исполнение не проверены.", "error"));
      if (state.demoResultError || data?.error) section.append(node("p", state.demoResultError || data.error, "description"));
      continue;
    }
    const request = state.requests.find((r) => r.id === data.dealId);
    const model = request ? summarize(request) : null;
    const execution = data.execution || {}, confirmed = execution.status === "CONFIRMED";
    const heading = node("div", undefined, "result-heading"), title = node("div");
    title.append(node("p", "РЕЗУЛЬТАТ КОНКРЕТНОЙ СДЕЛКИ", "eyebrow"), node("h3", `Сделка #${data.displayId || 742} · Факторинг`));
    
    const actions = node("div", undefined, "action-group"); actions.append(badge(statusTitle(data.status), confirmed ? "success" : "muted"), button("Открыть сделку →", () => showDeal(data.dealId))); heading.append(title, actions);
    section.append(heading);
    const facts = Object.entries(data.checks || {}), passed = facts.filter(([, status]) => status === "VERIFIED").length;
    const metrics = node("div", undefined, "result-metrics");
    const amount = confirmed ? execution.amount : data.settlement?.financingAmount;
    const decimals = data.settlement?.decimals ?? 6;
    const financingMoney = typeof amount === "number" ? new Intl.NumberFormat("ru-RU", {maximumFractionDigits: decimals}).format(amount / (10 ** decimals)) + " ₽" : "Нет данных";
    metrics.append(resultMetric("Сумма требования", money(data.settlement?.claimAmount)), resultMetric("Подтверждения", facts.length ? `${passed} из ${facts.length}` : "Нет данных", confirmed ? "При выплате" : "Текущая проверка"),
      resultMetric("Сумма финансирования", financingMoney, confirmed ? "Выплачено поставщику" : "По условиям сделки"),
      resultMetric("Оракул", model?.signature === "SIGNED" ? "Подписано" : model?.signature === "EXPIRED" ? "Подпись истекла" : "Не подписано"));
    section.append(metrics);
    const progress = node("div", undefined, "result-progress result-progress-compact");
    const oracleText = {READY: "Подписанное подтверждение выдано", WAITING: "Ожидание обязательных подтверждений", REJECTED: "Проверка условий отклонена"};
    progress.append(node("p", `Оракул: ${confirmed ? "подтверждение использовано при выплате" : model?.signature === "EXPIRED" ? "срок подписи истёк" : oracleText[model?.evaluation || data.evaluationStatus] || "нет данных"}`));
    progress.append(node("p", confirmed ? "Контракт: транзакция исполнена, аванс перечислен поставщику." : execution.status === "NOT_EXECUTED" ? "Контракт: выплата ещё не исполнена." : "Контракт: подтверждение транзакции недоступно. Статус оплаты не подтверждён."));
    section.append(progress);
    if (model) {section.append(renderStages(model), renderReasons(model));if (confirmed) section.append(transactionDetails(model));}
    section.append(button("Ответы источников →", () => openResponses({requestId: data.dealId})));
  }
}
