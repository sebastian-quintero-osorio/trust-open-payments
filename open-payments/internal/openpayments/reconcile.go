package openpayments

import (
	"context"
	"errors"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"net/url"
)

// FindOutgoing reconciles an uncertain create by its unique persisted operation ID.
// Absence is not proof of non-execution: callers must keep polling, never re-POST.
func (c *Client) FindOutgoing(ctx context.Context, w Wallet, t config.Secret, operationID string) (Outgoing, bool, error) {
	cursor := ""
	for range 20 {
		u, e := url.Parse(resource(w.ResourceServer, "outgoing-payments"))
		if e != nil {
			return Outgoing{}, false, e
		}
		q := u.Query()
		q.Set("wallet-address", w.ID)
		q.Set("first", "100")
		if cursor != "" {
			q.Set("cursor", cursor)
		}
		u.RawQuery = q.Encode()
		var page struct {
			Result     []Outgoing `json:"result"`
			Pagination struct {
				HasNextPage bool   `json:"hasNextPage"`
				EndCursor   string `json:"endCursor"`
			} `json:"pagination"`
		}
		if e = c.request(ctx, "GET", u.String(), t, nil, &page); e != nil {
			return Outgoing{}, false, e
		}
		for _, o := range page.Result {
			if o.Metadata["op_operation_id"] == operationID {
				if !SameOrigin(o.ID, w.ResourceServer) {
					return Outgoing{}, false, errors.New("invalid outgoing origin")
				}
				return o, true, nil
			}
		}
		if !page.Pagination.HasNextPage {
			return Outgoing{}, false, nil
		}
		if page.Pagination.EndCursor == "" || page.Pagination.EndCursor == cursor {
			return Outgoing{}, false, errors.New("invalid outgoing pagination")
		}
		cursor = page.Pagination.EndCursor
	}
	return Outgoing{}, false, nil
}
