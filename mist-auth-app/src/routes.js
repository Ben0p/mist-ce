import { Router } from '@vaadin/router';
import './pages/page-sign-in.js';

const outlet = document.getElementById('outlet');

const router = new Router(outlet);
router.setRoutes([
  {
    path: '/',
    component: 'page-sign-in',
  },
  {
    path: '/sign-in',
    component: 'page-sign-in',
  },
  // Additional routes can be added here
]);