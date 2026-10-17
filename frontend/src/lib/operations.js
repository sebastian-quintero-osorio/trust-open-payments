// Generated from ../trust-api.openapi.yaml by scripts/contract.py.
export const operations = {
  "register": {
    "method": "POST",
    "path": "/api/v1/auth/register",
    "public": true
  },
  "login": {
    "method": "POST",
    "path": "/api/v1/auth/login",
    "public": true
  },
  "getMe": {
    "method": "GET",
    "path": "/api/v1/me",
    "public": false
  },
  "updateMe": {
    "method": "PATCH",
    "path": "/api/v1/me",
    "public": false
  },
  "setPayoutWallet": {
    "method": "PUT",
    "path": "/api/v1/me/payout-wallet",
    "public": false
  },
  "getCollectionAccount": {
    "method": "GET",
    "path": "/api/v1/me/collection-account",
    "public": false
  },
  "createCollectionAccount": {
    "method": "POST",
    "path": "/api/v1/me/collection-account",
    "public": false
  },
  "getMyCreditProfile": {
    "method": "GET",
    "path": "/api/v1/me/credit-profile",
    "public": false
  },
  "getMerchantCreditProfile": {
    "method": "GET",
    "path": "/api/v1/merchants/{merchant_id}/credit-profile",
    "public": false
  },
  "createLoanRequest": {
    "method": "POST",
    "path": "/api/v1/loan-requests",
    "public": false
  },
  "listLoanRequests": {
    "method": "GET",
    "path": "/api/v1/loan-requests",
    "public": false
  },
  "getLoanRequest": {
    "method": "GET",
    "path": "/api/v1/loan-requests/{request_id}",
    "public": false
  },
  "cancelLoanRequest": {
    "method": "POST",
    "path": "/api/v1/loan-requests/{request_id}/cancel",
    "public": false
  },
  "createLoanOffer": {
    "method": "POST",
    "path": "/api/v1/loan-offers",
    "public": false
  },
  "listLoanOffers": {
    "method": "GET",
    "path": "/api/v1/loan-offers",
    "public": false
  },
  "getLoanOffer": {
    "method": "GET",
    "path": "/api/v1/loan-offers/{offer_id}",
    "public": false
  },
  "cancelLoanOffer": {
    "method": "POST",
    "path": "/api/v1/loan-offers/{offer_id}/cancel",
    "public": false
  },
  "listMatches": {
    "method": "GET",
    "path": "/api/v1/matches",
    "public": false
  },
  "previewLoan": {
    "method": "POST",
    "path": "/api/v1/loans/preview",
    "public": false
  },
  "createLoan": {
    "method": "POST",
    "path": "/api/v1/loans",
    "public": false
  },
  "listLoans": {
    "method": "GET",
    "path": "/api/v1/loans",
    "public": false
  },
  "getLoan": {
    "method": "GET",
    "path": "/api/v1/loans/{loan_id}",
    "public": false
  },
  "acceptLoan": {
    "method": "POST",
    "path": "/api/v1/loans/{loan_id}/accept",
    "public": false
  },
  "rejectLoan": {
    "method": "POST",
    "path": "/api/v1/loans/{loan_id}/reject",
    "public": false
  },
  "cancelLoan": {
    "method": "POST",
    "path": "/api/v1/loans/{loan_id}/cancel",
    "public": false
  },
  "disburseLoan": {
    "method": "POST",
    "path": "/api/v1/loans/{loan_id}/disburse",
    "public": false
  },
  "getLoanPctHistory": {
    "method": "GET",
    "path": "/api/v1/loans/{loan_id}/pct-history",
    "public": false
  },
  "getSalesToday": {
    "method": "GET",
    "path": "/api/v1/sales/today",
    "public": false
  },
  "listSales": {
    "method": "GET",
    "path": "/api/v1/sales",
    "public": false
  },
  "getSale": {
    "method": "GET",
    "path": "/api/v1/sales/{sale_id}",
    "public": false
  },
  "puntoredPaymentWebhook": {
    "method": "POST",
    "path": "/api/v1/webhooks/puntored/payments",
    "public": true
  },
  "listRepayments": {
    "method": "GET",
    "path": "/api/v1/repayments",
    "public": false
  },
  "getRepaymentTrace": {
    "method": "GET",
    "path": "/api/v1/repayments/{repayment_id}",
    "public": false
  },
  "getMerchantDashboard": {
    "method": "GET",
    "path": "/api/v1/dashboard/merchant",
    "public": false
  },
  "getLenderDashboard": {
    "method": "GET",
    "path": "/api/v1/dashboard/lender",
    "public": false
  },
  "listNotifications": {
    "method": "GET",
    "path": "/api/v1/notifications",
    "public": false
  },
  "readAllNotifications": {
    "method": "POST",
    "path": "/api/v1/notifications/read-all",
    "public": false
  },
  "readNotification": {
    "method": "POST",
    "path": "/api/v1/notifications/{notification_id}/read",
    "public": false
  },
  "joinWaitlist": {
    "method": "POST",
    "path": "/api/v1/waitlist",
    "public": true
  },
  "getAdminDashboard": {
    "method": "GET",
    "path": "/api/v1/admin/dashboard",
    "public": false
  },
  "retryPayout": {
    "method": "POST",
    "path": "/api/v1/admin/payouts/{payout_id}/retry",
    "public": false
  },
  "revokeCollectionAccount": {
    "method": "POST",
    "path": "/api/v1/admin/collection-accounts/{collection_account_id}/revoke",
    "public": false
  },
  "getHealth": {
    "method": "GET",
    "path": "/api/v1/health",
    "public": true
  },
  "getConfig": {
    "method": "GET",
    "path": "/api/v1/config",
    "public": true
  },
  "devSeed": {
    "method": "POST",
    "path": "/api/v1/dev/seed",
    "public": true
  },
  "devSimulateSale": {
    "method": "POST",
    "path": "/api/v1/dev/sales/simulate",
    "public": true
  },
  "devClosePeriod": {
    "method": "POST",
    "path": "/api/v1/dev/loans/{loan_id}/close-period",
    "public": true
  },
  "devFakeWalletAuthorize": {
    "method": "GET",
    "path": "/api/v1/dev/fake-wallet/authorize/{payment_id}",
    "public": true
  }
};
