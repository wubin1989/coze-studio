package service

import (
	"context"
	"errors"
)

type mcpCallImpl struct {
}

func (m *mcpCallImpl) Do(ctx context.Context, args map[string]any) (request string, resp string, err error) {
	return "", "", errors.New("mcp call not implemented")
}
