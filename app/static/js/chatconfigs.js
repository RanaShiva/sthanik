// The three conversational models, as configurations for createChat. The Chat page shows one at a time.
import { createChat } from './chatview.js';

export const CHAT_MODELS = [
  {
    key: 'text', label: 'Text', icon: 'chat', model: 'LFM3-1.7B', sub: 'text in, text out',
    make: ({ onNeed } = {}) => createChat({
      model: 'text',
      dictation: true, speak: true, onNeed,
      needs: { attach: { model: 'omni', why: 'This model reads text only. Images, video and audio files need Omni (click to switch).' } },
      emptyIcon: 'chat',
      emptyTitle: 'Talk to LFM3-1.7B',
      emptyText: 'A fast reasoning model with a 256K-token context. Switch on Search or Tools below and it can look things up, do exact maths and use your MCP servers. Choose Complex and it makes its own to-do list, researches each item and checks its own work.',
      suggestions: [
        { icon: 'globe', label: 'What are the latest news about open-weight AI models?', prompt: 'What are the latest news about open-weight AI models? Search the web and cite sources.' },
        { icon: 'listchecks', label: 'Write a 10-section report on running AI locally (Complex).', prompt: 'Write a detailed 10-section report on the pros and cons of running AI models locally for small businesses.' },
        { icon: 'workflow', label: 'Draw a flowchart of how a support ticket is handled.', prompt: 'Draw a flowchart of how a customer support ticket moves from submission to resolution, with an escalation branch.' },
        { icon: 'calculator', label: 'Work out 18% tip on a €86.40 bill, split 3 ways.', prompt: 'Work out an 18% tip on a €86.40 bill, split 3 ways. Use the calculator.' },
      ],
      tools: true,
      placeholder: 'Message LFM3-1.7B…',
      thinking: true,
      sampling: { max_tokens: 4096 },
    }),
  },
  {
    key: 'omni', label: 'Omni', icon: 'sparkles', model: 'LFM3-Omni-2B', sub: 'text, image, video and audio in; text and speech out',
    make: () => createChat({
      model: 'omni',
      dictation: true, // the mic writes what you say into the box (Omni transcribes it); you edit or add more, then send
      emptyIcon: 'sparkles',
      emptyTitle: 'One model, every modality',
      emptyText: 'Attach images, video (its soundtrack is heard too) or audio, speak with the mic, and optionally have replies read aloud in the model’s own voice.',
      suggestions: [
        { icon: 'video', label: 'What is happening in this video, and what is being said?', prompt: 'What is happening in this video, and what is being said?' },
        { icon: 'image', label: 'Describe this image, then read the description aloud.', prompt: 'Describe this image in two sentences.' },
        { icon: 'audio', label: 'Transcribe this recording.', prompt: 'Transcribe this recording.' },
        { icon: 'mic', label: 'Answer the question I asked in my voice message.', prompt: '' },
      ],
      tools: true,
      attach: ['image', 'video', 'audio'],
      speak: true,
      videoSystem: 'Answer questions about the video using what you see and hear.',
      placeholder: 'Ask anything — attach media or hit the mic…',
      sampling: { temperature: 0, repeat_penalty: 1, top_k: 0, max_tokens: 1024 },
    }),
  },
];

// Vision has its own page: it is a different kind of job (look at something, ask about it).
export const VISION = {
  key: 'vl', label: 'Vision', icon: 'eye', model: 'LFM3-VL-2B', sub: 'images and video in, text out',
  make: () => createChat({
    model: 'vl',
    emptyIcon: 'eye',
    emptyTitle: 'Show LFM3-VL-2B something',
    emptyText: 'Drop, paste or attach an image or a short video, then ask about it: description, OCR, counting, charts, scene understanding.',
    suggestions: [
      { icon: 'image', label: 'Describe this image in detail.', prompt: 'Describe this image in detail.' },
      { icon: 'text', label: 'Extract all the text you can see.', prompt: 'Extract all the text you can see.' },
      { icon: 'eye', label: 'How many people or objects are in the scene?', prompt: 'How many people or objects are in the scene?' },
      { icon: 'video', label: 'Summarise what happens in this video.', prompt: 'Summarise what happens in this video.' },
    ],
    // Looking at what you attached is the whole job: no web search, tools or pictures brought back from the web.
    tools: false,
    attach: ['image', 'video'],
    placeholder: 'Ask about the attached image or video…',
    thinking: false,
    sampling: { max_tokens: 4096 },
  }),
};
