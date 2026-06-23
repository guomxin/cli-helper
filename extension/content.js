chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "bscli.domSnapshot") {
    return false;
  }
  const selector = message.selector || "body";
  const element = document.querySelector(selector) || document.body;
  sendResponse({
    url: location.href,
    title: document.title,
    selector,
    text: (element.innerText || "").slice(0, 20000),
  });
  return true;
});
