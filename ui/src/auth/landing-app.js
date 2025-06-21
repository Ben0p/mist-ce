import '@polymer/polymer/polymer-legacy.js';
import '@polymer/app-route/app-location.js';
import '@polymer/app-route/app-route.js';
import '@polymer/iron-pages/iron-pages.js';

import './landing-sign-in.js';
import './landing-sign-up.js';
import './landing-forgot-password.js';
import './landing-set-password.js';
import './landing-reset-password.js';
import './shared-styles.js';
import './mist-theme.js'

import { Polymer } from '@polymer/polymer/lib/legacy/polymer-fn.js';
import { html } from '@polymer/polymer/lib/utils/html-tag.js';

Polymer({
  _template: html`
    <style>
      :host {
        display: block;
        width: 100%;
        height: 100vh;
        box-sizing: border-box;
      }
    </style>

    <!-- URL binding -->
    <app-location route="{{route}}"></app-location>
    <app-route
      route="{{route}}"
      pattern="/:page"
      data="{{routeData}}"
    ></app-route>

    <!-- Simple page switcher -->
    <iron-pages
      selected="{{page}}"
      attr-for-selected="name"
      fallback-selection="sign-in"
      role="main"
    >
      <landing-sign-in
        name="sign-in"
        route="[[route]]"
        sign-in-google=[[config.features.signin_google]]
        sign-in-github=[[config.features.signin_github]]
        sign-in-email=[[config.features.signin_email]]
        sign-in-ldap=[[config.features.signin_ldap]]
        sign-in-a-d=[[config.features.signin_ad]]
        sign-in-ms365=[[config.features.signin_ms365]]
        sign-in-c-i-logon=[[config.features.signin_cilogon]]
        default-method=[[config.features.default_signin_method]]
        invitoken="[[invitoken]]"
        return-to="[[returnTo]]"
        csrf-token="[[csrfToken]]"
      ></landing-sign-in>
      <landing-sign-up name="sign-up" route="[[route]]"></landing-sign-up>
      <landing-forgot-password name="forgot-password" route="[[route]]"></landing-forgot-password>
      <landing-set-password name="set-password" route="[[route]]"></landing-set-password>
      <landing-reset-password name="reset-password" route="[[route]]"></landing-reset-password>
    </iron-pages>
  `,

  is: 'landing-app',

  properties: {
    route: Object,
    routeData: Object,
    page: {
      type: String,
      reflectToAttribute: true,
      value: 'sign-in'
    },
    config: {
      type: Object,
      value() {
        return {};
      }
    }
  },

  observers: [
    '_routePageChanged(routeData.page)'
  ],

  _routePageChanged(page) {
    // default to sign-in when no page param
    this.page = page || 'sign-in';
  }
});