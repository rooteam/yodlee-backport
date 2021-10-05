odoo.define('account_yodlee_rt.acc_config_widget_ext', function(require) {
"use strict";

    var core = require('web.core');
    var framework = require('web.framework');
    var Widget = require('web.Widget');
    var Yodlee = core.action_registry.get('yodlee_online_sync_widget');
    var QWeb = core.qweb;

    Yodlee.include({

         init: function(parent, context) {
            this._super(parent, context);
            this.login_form = context.login_form;
            this.refresh_info = context.refresh_info;
            this.in_rpc_call = false;
            this.userToken = context.userToken;
            this.fastlinkUrl = context.fastlinkUrl;
            this.accessTokens = context.accessTokens;
            this.beta = context.beta;
            this.state = context.state;
            this.callbackUrl = context.paramsUrl +
                document.location.protocol + '//' + document.location.host + context.callbackUrl;
            // In case we launch wizard in an advanced step (like updating credentials or mfa)
            // We need to set this.init_call to false and this.id (both should be in context)
            this.init_call = true;
            this.context = context.context;
            if (context.context.init_call !== undefined) {
                this.init_call = context.context.init_call;
            }
            if (context.context.provider_account_identifier !== undefined) {
                this.id = context.context.provider_account_identifier;
            }
            if (context.context.open_action_end !== undefined) {
                this.action_end = context.context.open_action_end;
            }
        },

        renderButtons: function($node) {
            var self = this;
            if (this.userToken !== undefined) {
                this.$buttons = $(QWeb.render("YodleeTemplateFooter", {'widget': this}));
                this.$buttons.find('.js_yodlee_continue').click(function (e) {
                    self.$('#yodleeForm').submit();
                });
                this.$buttons.appendTo($node);
            }
        },

        renderElement: function() {
            var self = this;
            var fields = {};
            if (this.refresh_info && (
                    this.refresh_info.providerAccount.refreshInfo.status === 'SUCCESS' ||
                    this.refresh_info.providerAccount.refreshInfo.status === 'PARTIAL_SUCCESS')
               ) {
                if (this.action_end) {
                    return this._rpc({
                            model: 'account.online.provider',
                            method: 'open_action',
                            args: [[self.id], this.action_end, this.refresh_info.numberAccountAdded, this.context],
                        })
                        .then(function(result) {
                            self.do_action(result);
                        });
                }
                else {
                    var local_dict = {
                                    init_call: this.init_call, 
                                    number_added: this.refresh_info.numberAccountAdded,
                                    transactions: this.refresh_info.transactions,};
                    self.replaceElement($(QWeb.render('Success', local_dict)));
                }
            }
            else {
                /*if (this.login_form) {
                    console.log(this.login_form)
                    fields = this.login_form.provider[0];
                }*/
                if (this.refresh_info !== undefined) {
                    fields = this.refresh_info.providerAccount;
                    self.parse_image();
                }
                this.replaceElement($(QWeb.render('YodleeTemplateRt', {widget: self})));
            }
            this.renderButtons();
        },
    });


});