package main

import (
	"crypto/md5"
	"fmt"
	"strings"
	"time"
)

// Message struct to match our message structure
type Message struct {
	ID            int
	ConversationID int
	Content       string
	Time          int64
}

// Generate sample messages
func generateSampleMessages(count int) []Message {
	messages := make([]Message, count)
	now := time.Now().UnixMilli()
	
	for i := 0; i < count; i++ {
		messages[i] = Message{
			ID:            i,
			ConversationID: 1,
			Content:       fmt.Sprintf("This is test message number %d with some additional content to make it more realistic and have varying lengths %f", i, float64(i)),
			Time:          now + int64(i*1000),
		}
	}
	return messages
}

// Custom simple hash function
func customHash(messages []Message) string {
	var builder strings.Builder
	for _, msg := range messages {
		builder.WriteString(fmt.Sprintf("%d%c%c%d",
			len(msg.Content),
			msg.Content[0],
			msg.Content[len(msg.Content)-1],
			msg.Time))
	}
	return builder.String()
}

// MD5 hash function using all message data
func md5Hash(messages []Message) string {
	var builder strings.Builder
	for _, msg := range messages {
		builder.WriteString(fmt.Sprintf("%s%d", msg.Content, msg.Time))
	}
	sum := md5.Sum([]byte(builder.String()))
	return fmt.Sprintf("%x", sum)
}

func main() {
	iterations := 10000
	messages := generateSampleMessages(20)

	fmt.Println("Starting benchmark...")
	fmt.Printf("Running %d iterations with 20 messages each\n\n", iterations)

	// Custom hash benchmark
	customStart := time.Now()
	for i := 0; i < iterations; i++ {
		customHash(messages)
	}
	customDuration := time.Since(customStart)

	// MD5 hash benchmark
	md5Start := time.Now()
	for i := 0; i < iterations; i++ {
		md5Hash(messages)
	}
	md5Duration := time.Since(md5Start)

	fmt.Println("Results:")
	fmt.Printf("Custom Hash: %v (%v per iteration)\n", customDuration, customDuration/time.Duration(iterations))
	fmt.Printf("MD5 Hash: %v (%v per iteration)\n", md5Duration, md5Duration/time.Duration(iterations))
	fmt.Printf("\nCustom hash is %.2fx faster than MD5\n", float64(md5Duration)/float64(customDuration))

	// Print example hashes
	fmt.Println("\nExample hashes:")
	fmt.Println("Custom:", customHash(messages))
	fmt.Println("MD5:", md5Hash(messages))
}
