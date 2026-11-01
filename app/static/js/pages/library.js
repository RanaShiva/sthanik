import { h } from '../ui.js';
import { tabbedPage } from './tabbed.js';
import { memoryPanel } from '../memorypanel.js';

// Everything the app has made or learned: documents, pictures, and what it remembers about you.
export default tabbedPage({
  id: 'library',
  title: 'Library',
  subtitle: 'Your documents, pictures and the facts the model remembers',
  tabs: [
    ['documents', 'Documents', 'filetext', () => import('./documents.js')],
    ['images', 'Pictures', 'image', () => import('./images.js')],
    ['memory', 'Memory', 'book', () => Promise.resolve({ mount: (v) => { v.append(memoryPanel()); } })],
  ],
});
