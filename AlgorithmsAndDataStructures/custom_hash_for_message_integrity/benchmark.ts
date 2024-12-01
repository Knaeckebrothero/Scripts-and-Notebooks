import { Md5 } from 'ts-md5';

// Interface to match our message structure
interface Message {
    id: number;
    conversationId: number;
    content: string;
    time: number;
}

// Generate sample messages
function generateSampleMessages(count: number): Message[] {
    const messages: Message[] = [];
    const now = Date.now();
    
    for (let i = 0; i < count; i++) {
        messages.push({
            id: i,
            conversationId: 1,
            content: `This is test message number ${i} with some additional content to make it more realistic and have varying lengths ${Math.random()}`,
            time: now + i * 1000
        });
    }
    return messages;
}

// Custom simple hash function
function customHash(messages: Message[]): string {
    return messages.map(msg => 
        `${msg.content.length}${msg.content.charAt(0)}${msg.content.slice(-1)}${msg.time}`
    ).join('');
}

// MD5 hash function using all message data
function md5Hash(messages: Message[]): string {
    const messageString = messages.map(msg => 
        `${msg.content}${msg.time}`
    ).join('');
    return Md5.hashStr(messageString);
}

// Benchmark function
function runBenchmark(iterations: number = 10000) {
    const messages = generateSampleMessages(20);
    
    console.log('Starting benchmark...');
    console.log(`Running ${iterations} iterations with 20 messages each\n`);

    // Custom hash benchmark
    const customStart = performance.now();
    for (let i = 0; i < iterations; i++) {
        customHash(messages);
    }
    const customEnd = performance.now();
    const customTime = customEnd - customStart;

    // MD5 hash benchmark
    const md5Start = performance.now();
    for (let i = 0; i < iterations; i++) {
        md5Hash(messages);
    }
    const md5End = performance.now();
    const md5Time = md5End - md5Start;

    console.log('Results:');
    console.log(`Custom Hash: ${customTime.toFixed(2)}ms (${(customTime/iterations).toFixed(3)}ms per iteration)`);
    console.log(`MD5 Hash: ${md5Time.toFixed(2)}ms (${(md5Time/iterations).toFixed(3)}ms per iteration)`);
    console.log(`\nCustom hash is ${(md5Time/customTime).toFixed(2)}x faster than MD5`);

    // Print example hashes
    console.log('\nExample hashes:');
    console.log('Custom:', customHash(messages));
    console.log('MD5:', md5Hash(messages));
}

// Run the benchmark
runBenchmark();
