const factTitles = {
  DELIVERY_ACCEPTED: "Поставка принята", BUYER_NOTIFIED: "Покупатель уведомлён",
  CLAIM_ASSIGNED: "Уступка подтверждена", FINANCING_APPROVED: "Финансирование одобрено"
};
const fieldTitles = {
  supplier: "Поставщик", buyer: "Покупатель", factor: "Фактор", claimAmount: "Сумма требования",
  financingAmount: "Сумма аванса", commissionAmount: "Комиссия", dueDate: "Срок оплаты",
  buyerAuthorized: "Полномочия покупателя", acceptance: "Статус приёмки",
  notificationStatus: "Статус уведомления", claimId: "Номер требования", conditions: "Условия финансирования",
  documentHash: "Хеш документа", subjectRef: "Номер / название", dealId: "Идентификатор сделки"
};
function factTitle(fact) { return Object.hasOwn(factTitles, fact) ? factTitles[fact] : fact; }
function fieldTitle(field) { return Object.hasOwn(fieldTitles, field) ? fieldTitles[field] : field; }
function displayValue(value) {
  if (value === true) return "Да";
  if (value === false) return "Нет";
  if (value === null) return "Нет значения";
  if (typeof value === "string") return ({signed: "Подписан", received: "Получено", unconditional: "Без дополнительных условий"})[value] || value;
  if (Array.isArray(value)) return value.map(displayValue).join(", ");
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function conditionText(rule) {
  const c = rule.condition || {op: "eq", value: rule.equals};
  if (c.op === "eq" && !Object.hasOwn(c, "context") && typeof c.value === "boolean") return c.value ? "Источник должен подтвердить факт" : "Ожидается отрицательное значение: «Нет»";
  const verbs = {eq: "равно", ne: "не равно", gt: "больше", gte: "не меньше", lt: "меньше", lte: "не больше", in: "входит в список"};
  return `Значение ${verbs[c.op] || c.op} ${Object.hasOwn(c, "context") ? `реквизиту «${fieldTitle(c.context)}»` : `«${displayValue(c.value)}»`}`;
}
function termDescriptions(rule) {
  return [...Object.entries(rule.attributes || {}).map(([key, path]) => `${fieldTitle(key)} — совпадает с реквизитом «${fieldTitle(path)}»`),
    ...Object.entries(rule.required || {}).map(([key, value]) => `${fieldTitle(key)}: ${displayValue(value)}`)];
}
function readableTerms(rule) {
  const list = node("ul", undefined, "business-terms");
  for (const text of termDescriptions(rule)) list.append(node("li", text));
  return list;
}

function sourceTitle(source) {
  return (source?.name || source?.sourceId || "Источник не определён").replace(/^(?:mock|мок)\s+/iu, "");
}
function statusTitle(status) {
  return ({ACTIVE: "Создана", RESERVED: "Аванс в резерве", FINANCED: "Финансирование исполнено", SETTLED: "Задолженность погашена",
    READY: "Готово", WAITING: "Ожидание", REJECTED: "Отклонено", UNAVAILABLE: "Нет данных", NONE: "Не создана",
    VERIFIED: "Подтверждено", NOT_CHECKED: "Не проверено"})[status] || "Нет данных";
}

function retrievalTitle(status) {
  return ({present: "Получено", unknown: "Неизвестно", unavailable: "Недоступно", rejected: "Отклонено"})[status] || "Не сохранено";
}
