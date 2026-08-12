import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { apiRequest } from "../api/client";
import { useChatStream, type ChatStreamAttachment } from "./useChatStream";

export type PendingAttachment = {
  id: string;
  name: string;
  size: string;
  file: File;
  uploading?: boolean;
  previewUrl?: string;
};

type Toast = (options: { kind: "warning" | "error"; title: string; body: string }) => void;

type UseWorkbenchSendParams = {
  workspaceId: string | null;
  sessionId: string | null;
  input: string;
  attachments: PendingAttachment[];
  sending: boolean;
  visionSupported?: boolean;
  setInput: Dispatch<SetStateAction<string>>;
  setAttachments: Dispatch<SetStateAction<PendingAttachment[]>>;
  clearDraft: () => void;
  prepareToSend: () => void;
  keepAtBottom: () => void;
  toast: Toast;
  pendingAutoMetadataRef: MutableRefObject<Record<string, unknown> | null>;
};

/**
 * Page-level turn preparation for the workbench.
 *
 * Owns the UI-to-stream boundary: validation, scratch-session creation and
 * attachment upload. Transport, streaming tokens and result reconciliation
 * stay in useChatStream. Keeping the boundary here prevents AgentWorkbench
 * from becoming a second message-runtime implementation.
 */
export function useWorkbenchSend({
  workspaceId,
  sessionId,
  input,
  attachments,
  sending,
  visionSupported,
  setInput,
  setAttachments,
  clearDraft,
  prepareToSend,
  keepAtBottom,
  toast,
  pendingAutoMetadataRef,
}: UseWorkbenchSendParams) {
  const { send: sendStream, stop } = useChatStream(
    { workspaceId, sessionId, llmHealth: { visionSupported } },
    {
      onSessionResolved: () => {},
      onResult: () => { keepAtBottom(); },
      onInterruption: (message) => {
        toast({ kind: "warning", title: "实时连接中断", body: message });
      },
    },
  );

  const send = useCallback(async (
    textOverride?: string,
    metadataOverride?: Record<string, unknown>,
  ) => {
    const pendingAttachments = attachments;
    const hasAttachments = pendingAttachments.length > 0;
    const hasImages = pendingAttachments.some((attachment) => attachment.file.type.startsWith("image/"));
    const text = (typeof textOverride === "string" ? textOverride : input).trim();
    if ((!text && !hasAttachments) || sending) return;
    if (!workspaceId) {
      toast({ kind: "warning", title: "未选择工作区", body: "请在左侧选择一个工作区" });
      return;
    }
    if (!sessionId) {
      toast({ kind: "warning", title: "请先新建会话", body: "点击左侧会话标题旁的“+”后再发送消息。" });
      return;
    }
    if (hasImages && visionSupported === false) {
      toast({
        kind: "warning",
        title: "当前模型不支持识图",
        body: "请在系统管理的模型设置中切换到支持图片输入的模型后再发送。图片仍保留在输入框中。",
      });
      return;
    }

    setInput("");
    clearDraft();
    const turnMetadata = { ...(metadataOverride || pendingAutoMetadataRef.current || {}) };
    pendingAutoMetadataRef.current = null;
    const effectiveSessionId = sessionId;
    let fullText = text;
    let displayAttachments: ChatStreamAttachment[] = [];

    if (hasAttachments) {
      setAttachments((previous) => previous.map((attachment) => ({ ...attachment, uploading: true })));
      const uploaded: ChatStreamAttachment[] = [];
      const failedNames: string[] = [];
      for (const attachment of pendingAttachments) {
        try {
          const form = new FormData();
          form.append("file", attachment.file);
          form.append("artifact_type", "chat_attachment");
          form.append("title", attachment.name);
          form.append("workspace_id", workspaceId);
          form.append("session_id", effectiveSessionId);
          const result = await apiRequest<{ ok: boolean; file: { file_id: string } }>({
            method: "POST",
            url: `/workspaces/${workspaceId}/artifacts/upload`,
            data: form,
          });
          if (!result.ok || !result.file?.file_id) {
            failedNames.push(attachment.name);
            continue;
          }
          const item: ChatStreamAttachment = {
            file_id: result.file.file_id,
            name: attachment.name,
            mime_type: attachment.file.type || "application/octet-stream",
            size_bytes: attachment.file.size,
            kind: attachment.file.type.startsWith("image/") ? "image" : "file",
            previewUrl: attachment.previewUrl,
          };
          uploaded.push(item);
        } catch { failedNames.push(attachment.name); }
      }
      setAttachments([]);
      if (!uploaded.length) {
        toast({ kind: "error", title: "附件上传失败", body: "未能上传附件，请稍后重试。" });
        return;
      }
      if (failedNames.length) {
        toast({ kind: "warning", title: "部分附件上传失败", body: failedNames.slice(0, 3).join("、") });
      }
      displayAttachments = uploaded;
      turnMetadata.attachments = uploaded.map(({ previewUrl: _previewUrl, ...item }) => item);
      if (!fullText) {
        fullText = "请分析已附加的内容。";
      }
    }

    prepareToSend();
    requestAnimationFrame(keepAtBottom);
    await sendStream({ text: fullText, attachments: displayAttachments, effectiveSessionId, turnMetadata });
  }, [attachments, clearDraft, input, keepAtBottom, pendingAutoMetadataRef, prepareToSend, sendStream, sending, sessionId, setAttachments, setInput, toast, visionSupported, workspaceId]);

  return { send, stop };
}
