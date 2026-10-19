export interface ReaderUser {
  id: number;
  username: string;
  nickname: string | null;
  avatar_url?: string | null;
}

export interface NovelCard {
  id: number;
  title: string;
  summary: string | null;
  cover: string | null;
  tags: string[];
  word_count: number;
  chapter_count: number;
  read_count: number;
  like_count: number;
  favorite_count: number;
  updated_at?: string | null;
  progress_chapter_id?: number | null;
  progress_chapter_title?: string | null;
  favorited?: boolean;
  liked?: boolean;
  visited_at?: string | null;
  progress_updated_at?: string | null;
}

export interface StoreHome {
  continue_reading: NovelCard[];
  hot: NovelCard[];
  newest: NovelCard[];
  for_you: NovelCard[];
  tags: { name: string; count: number }[];
}
