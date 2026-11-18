import { ChangeDetectionStrategy, Component, computed, input, model, output, signal } from '@angular/core';
import type { FormValueControl } from '@angular/forms/signals';
import { KitButton, KitErrorLine, KitTextInput } from 'fleet';

import { markIsImage } from './config-edit.model';

/**
 * A mark as a form control: emoji text, or a PNG/WebP image read into a data URL, with a preview, a clear, and its own
 * line for a file that could not be read. A read that finishes after the value was typed over or cleared is dropped.
 * Reports through `reading` while a file is being read, so the form can hold Save until it lands.
 */
@Component({
  selector: 'app-config-mark-input',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitErrorLine, KitTextInput],
  templateUrl: './config-mark-input.html',
  styleUrl: './config-mark-input.css',
})
export class ConfigMarkInput implements FormValueControl<string> {
  readonly value = model('');
  readonly disabled = input(false);
  readonly ariaLabel = input('Mark');
  readonly testid = input.required<string>();
  readonly maxImageBytes = input<number | undefined>();
  readonly invalidChange = output<boolean>();

  /** Whether a file is being read. */
  readonly reading = output<boolean>();

  protected readonly error = signal<string | null>(null);
  protected readonly isImage = computed(() => markIsImage(this.value()));
  private read = 0;

  protected onText(text: string): void {
    this.settle();
    this.error.set(null);
    this.invalidChange.emit(false);
    this.value.set(text);
  }

  protected onFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    const read = this.settle();
    if (file.type !== 'image/png' && file.type !== 'image/webp') {
      this.error.set('Choose a PNG or WebP image.');
      this.invalidChange.emit(true);
      return;
    }
    const max = this.maxImageBytes();
    if (max !== undefined && file.size > max) {
      this.error.set(`Image must be at most ${max} bytes.`);
      this.invalidChange.emit(true);
      return;
    }
    this.error.set(null);
    this.invalidChange.emit(false);
    this.reading.emit(true);
    const reader = new FileReader();
    reader.onload = () => {
      if (read === this.read && typeof reader.result === 'string') {
        this.reading.emit(false);
        this.value.set(reader.result);
      }
    };
    reader.onerror = () => {
      if (read === this.read) {
        this.reading.emit(false);
        this.error.set('Could not read the image.');
        this.invalidChange.emit(true);
      }
    };
    reader.readAsDataURL(file);
  }

  /** Abandons any read in flight and returns the id of the next one. */
  private settle(): number {
    this.read++;
    this.reading.emit(false);
    return this.read;
  }
}
