import { Component } from '@angular/core';
import { HttpClient } from '@angular/common/http';

interface ChatResponse {
  content: string;
}

@Component({
  selector: 'app-chat',
  templateUrl: './chat.component.html',
  styleUrls: ['./chat.component.scss']
})

export class ChatComponent {
  constructor(private http: HttpClient) { }
  response: ChatResponse | null = null;
  newMessage: string = '';
  messages = [
    { content: 'Test, left', alignment: 'left' },
    { content: 'Test, right', alignment: 'right' }
  ];

  sendMessage() {
    const payload = { content: this.newMessage };
    this.http.post<ChatResponse>('http://127.0.0.1:8000/api/chat', payload).subscribe({
      next: (response) => {
        this.messages.push({ content: response.content, alignment: 'left' });
      },
      error: (error) => {
        console.error('There was an error!', error);
        // Handle the error as you see fit
      }
    });
    
    this.messages.push({ content: this.newMessage, alignment: 'right' });
    this.newMessage = '';  // Clear the input field
  }
}
