import { VISION } from '../chatconfigs.js';
import { modelGate } from '../gate.js';

export default {
  title: 'Vision',
  subtitle: 'LFM3-VL-2B · look at images and video, ask about them',
  fill: true,
  keep: true, // state and any reply in progress survive navigating to another page
  mount(view) {
    const gate = modelGate('vl');
    const chat = VISION.make();
    view.append(gate, chat);
    return { destroy: () => { gate.destroy(); chat.destroy(); }, suspend: () => chat.suspend(), resume: () => chat.resume() };
  },
};
