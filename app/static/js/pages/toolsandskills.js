import { tabbedPage } from './tabbed.js';

export default tabbedPage({
  id: 'toolsandskills',
  title: 'Tools & Skills',
  subtitle: 'What the models can do: tools and connectors, and the playbooks they follow',
  tabs: [
    ['tools', 'Tools & connectors', 'plug', () => import('./tools.js')],
    ['skills', 'Skills', 'book', () => import('./skills.js')],
  ],
});
