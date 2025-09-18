package service

import (
	"context"
	"fmt"

	"github.com/cloudwego/eino/compose"
)

type CustomTool interface {
	Invoke(ctx context.Context, input map[string]any, opts ...compose.Option) (map[string]any, error)
}

var customToolMap = make(map[string]CustomTool)

func RegisterCustomTool(toolUniqueName string, t CustomTool) error {
	if _, ok := customToolMap[toolUniqueName]; ok {
		return fmt.Errorf("custom tool %s already registered", toolUniqueName)
	}

	customToolMap[toolUniqueName] = t

	return nil
}

// InvokableRun(ctx context.Context, argumentsInJSON string, opts ...Option) (string, error)
type customCallImpl struct {
}

func (c *customCallImpl) Do(ctx context.Context, args map[string]any) (request string, resp string, err error) {
	// if t == nil || t. == nil || t.Tool.Name == "" {
	// 	return "", "", fmt.Errorf("tool name is empty")
	// }
	// tool, ok := customToolMap[t.Tool.Name]
	// if !ok {
	// 	return "", "", fmt.Errorf("custom tool not found")
	// }
	// resp, err = tool.Invoke(ctx, args)
	// if err != nil {
	// 	return "", "", err
	// }
	// return "", resp, nil
	return "", "", fmt.Errorf("custom tool not implemented")
}
