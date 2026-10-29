import { createRequestButton } from './request-button.js?v=stop-1';

export const stoppedTask = task => !!(task?.local_stop_requested || task?.stop_requested || ['cancelled','canceled'].includes(task?.status));
export const requestVersion = owner => owner.requestVersion || 0;
export function beginMediaRequest(owner) {
  owner.requestVersion = requestVersion(owner) + 1;
  owner.stopping = false;
  owner.stopOperation = null;
  return owner.requestVersion;
}

export function captureMediaRequestFocus(owner) {
  owner.requestButtonHadFocus = document.activeElement === owner.requestButtonElement;
}

// Keep the same focused button across panel re-renders and polling updates.
export function mountMediaRequestButton(owner, placeholder, options, state, disabled) {
  if (owner.requestButtonElement) placeholder.replaceWith(owner.requestButtonElement);
  else {
    owner.requestButtonElement = placeholder;
    owner.requestButton = createRequestButton(placeholder, {...options, showLabel:true, initialState:state});
  }
  owner.requestButton.setState(state, {disabled});
  if(owner.requestButtonHadFocus && owner.requestButtonElement.isConnected)owner.requestButtonElement.focus({preventScroll:true});
  owner.requestButtonHadFocus = false;
  return owner.requestButtonElement;
}

export async function stopMediaRequest(owner, {api,json,target,render,resume}) {
  if (owner.stopping) return;
  const version = requestVersion(owner);
  const operation = owner.stopOperation = {};
  owner.pollVersion = (owner.pollVersion || 0) + 1;
  owner.stopping = true;
  render();
  let failed = false, finished = false, response;
  try {
    const result = await api('/api/generation/stop', json(target));
    if (requestVersion(owner) !== version) return;
    response = result;
    const finishedTask = ['completed','already_finished'].includes(result.status)
      ? result.tasks?.find(task => !stoppedTask(task) && ['done','succeeded','failed','blocked'].includes(task.status)) : null;
    finished = !!finishedTask;
    owner.requestVersion = version + 1;
    clearTimeout(owner.timer);
    owner.submitting = false;
    owner.status = finishedTask?.status || (['done','succeeded'].includes(owner.status) ? owner.status : 'cancelled');
    owner.uncertain = owner.submissionUnknown = false;
    owner.error = finished ? '' : result.message || '已停止当前请求';
  } catch (error) {
    if (requestVersion(owner) !== version) return;
    owner.error = '停止失败：' + error.message;
    failed = true;
  } finally {
    if (owner.stopOperation === operation) {
      owner.stopOperation = null;
      owner.stopping = false;
      render();
      if (failed || finished) resume?.(response);
    }
  }
}
